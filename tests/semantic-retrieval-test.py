"""Local semantic retrieval fixtures; no inference, internet, or personal content."""
from __future__ import annotations

import copy
from contextlib import closing
import json
import math
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import embedding_client as embedding
import semantic_retrieval as retrieval


def basis(index):
    vector=[0.0]*embedding.DIMENSIONS
    vector[index]=1.0
    return vector


def conceptual_vector(text):
    text=text.split('\nQuery: ')[-1].casefold()
    if any(word in text for word in ('boots','gummistøvler','bottes','waterproof')):return basis(0)
    if any(word in text for word in ('milk','mælk','dairy')):return basis(1)
    return basis(2)


class ConceptClient:
    def __init__(self):self.calls=[]
    def embed(self,inputs):
        self.calls.append(list(inputs))
        return [conceptual_vector(text) for text in inputs]


class GatedClient(ConceptClient):
    """Pause ingestion only; foreground query vectors remain available."""
    def __init__(self):
        super().__init__()
        self.started=threading.Event();self.release=threading.Event();self.timed_out=threading.Event()

    def embed(self,inputs):
        self.calls.append(list(inputs))
        if not any(text.startswith('Instruct:') for text in inputs):
            self.started.set()
            if not self.release.wait(3):
                self.timed_out.set()
                raise TimeoutError('Synthetic ingestion gate timed out')
        return [conceptual_vector(text) for text in inputs]


class Response:
    def __init__(self,payload=None,*,raw=None,status=200):
        self.status=status
        self.raw=raw if raw is not None else json.dumps(payload).encode()
    def __enter__(self):return self
    def __exit__(self,*_):pass
    def read(self,limit):return self.raw[:limit]


class Opener:
    def __init__(self):self.calls=[]
    def open(self,request,*,timeout):
        self.calls.append((request,timeout))
        body=json.loads(request.data)
        return Response({'data':[{'index':index,'embedding':conceptual_vector(text)}
                                 for index,text in enumerate(body['input'])]})


class EmbeddingTests(unittest.TestCase):
    def test_fixed_origin_batches_and_normalized_vectors(self):
        opener=Opener()
        result=embedding.EmbeddingClient(opener=opener).embed(['milk']*33)
        self.assertEqual(len(result),33)
        self.assertEqual([len(json.loads(request.data)['input']) for request,_ in opener.calls],[16,16,1])
        for request,timeout in opener.calls:
            self.assertEqual(request.full_url,embedding.ENDPOINT)
            self.assertEqual(request.method,'POST')
            self.assertEqual(json.loads(request.data)['model'],'embedding')
            self.assertEqual(json.loads(request.data)['encoding_format'],'float')
            self.assertGreater(timeout,0);self.assertLessEqual(timeout,6)
        self.assertTrue(all(len(row)==1024 and math.isclose(sum(x*x for x in row),1) for row in result))

    def test_response_indexes_are_exact_and_order_is_reconstructed(self):
        rows=[{'index':1,'embedding':basis(1)},{'index':0,'embedding':basis(0)}]
        self.assertEqual(embedding.vectors({'data':rows},2),[basis(0),basis(1)])
        for rows in ([{'index':0,'embedding':basis(0)}]*2,
                     [{'index':True,'embedding':basis(0)},{'index':0,'embedding':basis(0)}],
                     [{'index':-1,'embedding':basis(0)},{'index':0,'embedding':basis(0)}],
                     [{'index':0,'embedding':basis(0)}], [None,None]):
            with self.subTest(rows=str(rows)[:80]),self.assertRaises(ValueError):
                embedding.vectors({'data':rows},2)

    def test_invalid_dimensions_nonfinite_values_and_norm_are_refused(self):
        for vector in ([1.0], [0.0]*1024, [1.0]*1024,
                       [float('nan')]+[0.0]*1023,[float('inf')]+[0.0]*1023,
                       [True]+[0.0]*1023,['1']+[0.0]*1023):
            with self.subTest(value=str(vector)[:40]),self.assertRaises(ValueError):
                embedding.vectors({'data':[{'index':0,'embedding':vector}]},1)

    def test_input_timeout_response_status_and_size_budgets(self):
        opener=Opener();client=embedding.EmbeddingClient(opener=opener)
        for inputs in ([],['milk']*97,['x'*1901],[None],['']):
            with self.subTest(inputs=str(inputs)[:30]),self.assertRaises(ValueError):client.embed(inputs)
        self.assertEqual(opener.calls,[])
        with patch.object(embedding.time,'monotonic',side_effect=[0,7]):
            with self.assertRaises(TimeoutError):client.embed(['milk'])
        for response in (Response({},status=500),Response(raw=b'x'*(1024*1024+1)),Response(raw=b'not-json')):
            with patch.object(opener,'open',return_value=response),self.assertRaises(ValueError):client.embed(['milk'])
        with patch.object(opener,'open',side_effect=TimeoutError('synthetic')):
            with self.assertRaises(TimeoutError):client.embed(['milk'])
        self.assertIsNone(embedding.NoRedirect().redirect_request(None,None,None,None,None,None))

    def test_native_adapter_applies_instructions_only_to_queries(self):
        client=ConceptClient()
        document=embedding.openai_embeddings({'model':'embedding','input':['gummistøvler','milk'],
                                              'input_type':'document'},client)
        self.assertEqual(client.calls[-1],['gummistøvler','milk'])
        query=embedding.openai_embeddings({'model':'embedding','input':'waterproof shoes',
                                           'input_type':'query','dimensions':1024},client)
        self.assertTrue(client.calls[-1][0].startswith('Instruct: '))
        self.assertTrue(client.calls[-1][0].endswith('\nQuery: waterproof shoes'))
        self.assertEqual(query['data'][0]['index'],0)
        self.assertEqual(document['data'][1]['embedding'],basis(1))
        for args in ({'input_type':'text'},{'dimensions':512},{'encoding_format':'base64'},
                     {'model':'other'},{'url':'http://private'},{'input':[1,2,3]}):
            with self.subTest(args=args),self.assertRaises(ValueError):
                embedding.openai_embeddings({'model':'embedding','input':'milk','input_type':'document',**args},client)

    def test_native_adapter_chunks_long_documents_without_losing_tail(self):
        client=ConceptClient()
        text='opening words '*150+'FINAL_SENTENCE_GUMMISTØVLER'
        output=embedding.openai_embeddings({'model':'embedding','input':text,'input_type':'document'},client)
        embedded=[value for call in client.calls for value in call]
        self.assertGreater(len(embedded),1)
        self.assertTrue(any('FINAL_SENTENCE_GUMMISTØVLER' in value for value in embedded))
        self.assertFalse(any(value.startswith('Instruct:') for value in embedded))
        self.assertEqual(len(output['data']),1)
        self.assertAlmostEqual(sum(value*value for value in output['data'][0]['embedding']),1)

    def test_native_adapter_accepts_bounded_native_batches_and_rejects_excess(self):
        client=ConceptClient()
        result=embedding.openai_embeddings({'model':'embedding','input':['milk']*64,'input_type':'document'},client)
        self.assertEqual(len(result['data']),64)
        self.assertEqual([row['index'] for row in result['data']],list(range(64)))
        for inputs in (['milk']*65,['x'*32769],['x'*32768]*4,['x'*1500]*64):
            input_type='query' if len(inputs)==64 else 'document'
            with self.subTest(size=len(inputs),input_type=input_type),self.assertRaises(ValueError):
                embedding.openai_embeddings({'model':'embedding','input':inputs,'input_type':input_type},client)


class RetrievalTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name).resolve()
        self.client=ConceptClient();self.ranker=retrieval.SemanticRanker(self.client)

    def index(self,name='owner-school',ranker=None):
        return retrieval.RetrievalIndex(self.root/name/'archive.sqlite3',namespace=name,ranker=ranker or self.ranker)

    def records(self):
        return [{'id':'boots','text':'Gummistøvler til turen','metadata':{'source_tool':'synthetic'}},
                {'id':'milk','text':'Remember to buy milk','metadata':{'url':'https://example.com/source'}}]

    def test_semantic_synonyms_and_cross_language_ranking_keep_original_metadata(self):
        original=self.records();before=copy.deepcopy(original)
        result=self.ranker.rank('waterproof boots',original,task='school',limit=2)
        self.assertEqual(result[0]['id'],'boots')
        self.assertEqual(result[0]['lexical_score'],0)
        self.assertAlmostEqual(result[0]['semantic_score'],1)
        self.assertEqual(result[0]['metadata'],original[0]['metadata'])
        self.assertEqual(original,before)
        self.assertEqual(self.ranker.rank('mælk',original,task='shopping')[0]['id'],'milk')
        encoded=self.client.calls[0]
        self.assertTrue(encoded[0].startswith('Instruct: '))
        self.assertTrue(encoded[0].endswith('\nQuery: waterproof boots'))
        self.assertEqual(encoded[1:],['Gummistøvler til turen','Remember to buy milk'])

    def test_embedding_timeout_preserves_lexical_search_and_redacts_vectors(self):
        with patch.object(self.client,'embed',side_effect=TimeoutError('synthetic')):
            result=self.ranker.rank('milk',self.records(),task='memory')
        self.assertEqual(result[0]['id'],'milk')
        self.assertIsNone(result[0]['semantic_score'])
        self.assertGreater(result[0]['lexical_score'],0)
        self.assertFalse(self.ranker.available)
        self.assertNotIn('_vector',result[0])

    def test_malformed_embedding_responses_preserve_lexical_search(self):
        for payload in ({'data':[]},{'data':[{'index':0,'embedding':[1.0]}]},
                        {'data':[{'index':0,'embedding':[float('nan')]+[0.0]*1023}]}):
            opener=Opener()
            with patch.object(opener,'open',return_value=Response(payload)):
                ranker=retrieval.SemanticRanker(embedding.EmbeddingClient(opener=opener))
                result=ranker.rank('milk',self.records(),task='shopping')
            self.assertEqual(result[0]['id'],'milk')
            self.assertIsNone(result[0]['semantic_score'])
            self.assertFalse(ranker.available)

    def test_corrupt_cached_nonfinite_vectors_never_escape_as_scores(self):
        index=self.index();index.replace('source',self.records());index.search('milk',task='school')
        for vector in ([float('nan')]+[0.0]*1023,[1.0],['not-a-number']*1024,'malformed-json'):
            with closing(sqlite3.connect(index.path)) as db,db:
                encoded=vector if vector=='malformed-json' else json.dumps(vector)
                db.execute('UPDATE passages SET vector=?,revision=?',(encoded,retrieval.REVISION))
            rows=index.search('milk',task='school')
            self.assertEqual(rows[0]['text'],'Remember to buy milk')
            json.dumps(rows,allow_nan=False)

    def test_index_restart_reuses_vectors_and_only_embeds_query(self):
        index=self.index();index.replace('source',self.records())
        self.assertEqual(index.search('boots',task='school')[0]['metadata']['source_tool'],'synthetic')
        self.assertGreater(len(self.client.calls[0]),1)
        self.client.calls.clear()
        index.search('boots',task='school')
        self.assertEqual(len(self.client.calls),1)
        self.assertEqual(len(self.client.calls[0]),1)
        self.client.calls.clear()
        restarted=self.index();restarted.search('mælk',task='school')
        self.assertEqual(len(self.client.calls),1)
        self.assertEqual(len(self.client.calls[0]),1)

    def test_index_embedding_outage_uses_lexical_search_without_repeated_retry(self):
        index=self.index();index.replace('source',self.records())
        with patch.object(self.client,'embed',side_effect=TimeoutError('synthetic')) as embed:
            rows=index.search('milk',task='school')
        self.assertEqual(embed.call_count,1)
        self.assertEqual(rows[0]['text'],'Remember to buy milk')
        self.assertIsNone(rows[0]['semantic_score'])

    def test_semantic_budget_preserves_all_requested_lexical_matches(self):
        rows=[{'id':str(index),'text':'milk item '+str(index)} for index in range(120)]
        with patch.object(self.client,'embed',side_effect=TimeoutError('synthetic')):
            found=self.ranker.rank('milk',rows,task='shopping',limit=len(rows))
        self.assertEqual(len(found),len(rows))
        self.assertEqual({row['id'] for row in found},{row['id'] for row in rows})

    def test_replacement_updates_text_removes_stale_records_and_invalidates_changed_vectors(self):
        index=self.index();index.replace('source',self.records());index.search('boots',task='school')
        self.client.calls.clear()
        index.replace('source',[{'id':'milk','text':'Dairy delivery tomorrow','metadata':{'version':2}}])
        results=index.search('mælk',task='school')
        self.assertEqual(len(results),1)
        self.assertEqual(results[0]['text'],'Dairy delivery tomorrow')
        self.assertEqual(results[0]['metadata'],{'version':2})
        self.assertTrue(any('Dairy delivery tomorrow' in call for call in self.client.calls))
        index.replace('source',[])
        self.assertEqual(index.search('milk',task='school'),[])

    def test_revision_change_reembeds_documents_and_same_text_reuses_vectors(self):
        index=self.index();index.replace('source',self.records());index.search('milk',task='school')
        self.client.calls.clear()
        index.replace('source',self.records());index.search('milk',task='school')
        self.assertEqual(len(self.client.calls),1)
        self.client.calls.clear()
        with patch.object(retrieval,'REVISION','next-model-revision'):
            index.search('milk',task='school')
        self.assertTrue(any('Remember to buy milk' in call for call in self.client.calls))

    def test_deletion_removes_vectors_and_tombstones_prevent_replay_after_restart(self):
        index=self.index();index.replace('source',self.records());index.search('milk',task='school')
        index.delete('source')
        with closing(sqlite3.connect(index.path)) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM passages').fetchone()[0],0)
            self.assertEqual(db.execute('SELECT id FROM tombstones').fetchall(),[('source',)])
        restarted=self.index();restarted.replace('source',self.records())
        self.assertEqual(restarted.search('milk',task='school'),[])

    def test_ttl_expiration_removes_text_and_vectors(self):
        index=self.index()
        with patch.object(retrieval.time,'time',return_value=1000):index.replace('source',self.records(),ttl_seconds=60)
        with patch.object(retrieval.time,'time',return_value=1061):self.assertEqual(index.search('milk',task='school'),[])
        with closing(sqlite3.connect(index.path)) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM passages').fetchone()[0],0)

    def test_namespace_and_file_isolation(self):
        school=self.index();public=self.index('public-research')
        school.replace('source',self.records())
        self.assertEqual(public.search('milk',task='research'),[])
        with self.assertRaisesRegex(ValueError,'scope mismatch'):
            retrieval.RetrievalIndex(school.path,namespace='public-research',ranker=self.ranker)
        for namespace in ('owner/personal','OWNER','*','', 'owner_'*20):
            with self.subTest(namespace=namespace),self.assertRaises(ValueError):
                retrieval.RetrievalIndex(self.root/'other.sqlite3',namespace=namespace,ranker=self.ranker)

    def test_private_permissions_symlinks_and_hardlinks(self):
        index=self.index()
        self.assertEqual(index.path.stat().st_mode&0o777,0o600)
        self.assertEqual(index.path.parent.stat().st_mode&0o777,0o700)
        link=self.root/'linked.sqlite3';link.symlink_to(index.path)
        with self.assertRaisesRegex(ValueError,'symlink'):
            retrieval.RetrievalIndex(link,namespace='owner-school',ranker=self.ranker)
        directory=self.root/'linked-dir';directory.symlink_to(index.path.parent,target_is_directory=True)
        with self.assertRaisesRegex(ValueError,'symlink'):
            retrieval.RetrievalIndex(directory/'archive.sqlite3',namespace='owner-school',ranker=self.ranker)
        hardlink=index.path.parent/'hardlink.sqlite3';os.link(index.path,hardlink)
        with self.assertRaises(ValueError):self.index()
        hardlink.unlink();index.path.chmod(0o644)
        with self.assertRaises(ValueError):self.index()
        index.path.chmod(0o600);index.path.parent.chmod(0o755)
        with self.assertRaises(ValueError):self.index()

    def background_index(self):
        client=GatedClient();workers=[];thread_type=threading.Thread
        def tracked_thread(*args,**kwargs):
            worker=thread_type(*args,**kwargs);workers.append(worker);return worker
        def finish():
            client.release.set()
            for worker in workers:
                worker.join(3)
                self.assertFalse(worker.is_alive(),'Backfill worker must finish before fixture cleanup')
        self.addCleanup(finish)
        patcher=patch.object(retrieval.threading,'Thread',side_effect=tracked_thread)
        patcher.start();self.addCleanup(patcher.stop)
        index=retrieval.RetrievalIndex(self.root/'owner-school'/'archive.sqlite3',namespace='owner-school',
                                      ranker=retrieval.SemanticRanker(client),background=True)
        return index,client,workers

    def test_background_replacement_and_repeated_lexical_reads_do_not_wait_for_ingestion(self):
        index,client,workers=self.background_index()
        index.replace('source',[{'id':'item','text':'Gummistøvler til turen'}])
        self.assertTrue(client.started.wait(2),'Background ingestion did not begin')
        self.assertFalse(client.timed_out.is_set(),'replace blocked until inference timed out')
        index.replace('source',[{'id':'item','text':'Remember to buy milk tomorrow','metadata':{'version':2}}])
        for _ in range(2):
            found=index.search('milk',task='school')
            self.assertEqual(found[0]['text'],'Remember to buy milk tomorrow')
            self.assertEqual(found[0]['metadata'],{'version':2})
            self.assertIsNone(found[0]['semantic_score'])
        self.assertFalse(client.timed_out.is_set(),'A current read waited for the paused ingestion')
        self.assertFalse(client.release.is_set())
        self.assertEqual(len(workers),1,'Concurrent replace/search must reuse the active worker')
        # Ingestion contains only source text; queries are separate foreground calls.
        self.assertEqual(client.calls[0],['Gummistøvler til turen'])
        self.assertTrue(all(len(call)==1 and call[0].startswith('Instruct:') for call in client.calls[1:]))
        client.release.set();workers[0].join(3)
        self.assertFalse(workers[0].is_alive())
        with closing(sqlite3.connect(index.path)) as db:
            text,vector=db.execute('SELECT text,vector FROM passages').fetchone()
        self.assertEqual(text,'Remember to buy milk tomorrow')
        self.assertEqual(json.loads(vector),basis(1))

    def test_background_delete_during_inference_never_reinserts_content_or_vectors(self):
        index,client,workers=self.background_index()
        index.replace('source',self.records())
        self.assertTrue(client.started.wait(2))
        index.delete('source')
        with closing(sqlite3.connect(index.path)) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM passages').fetchone()[0],0)
            self.assertEqual(db.execute('SELECT id FROM tombstones').fetchall(),[('source',)])
        client.release.set();workers[0].join(3)
        self.assertFalse(workers[0].is_alive());self.assertFalse(client.timed_out.is_set())
        with closing(sqlite3.connect(index.path)) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM passages').fetchone()[0],0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM sources').fetchone()[0],0)
        index.replace('source',self.records())
        self.assertEqual(index.search('milk',task='school'),[])

    def test_warm_returns_bounded_counts_and_ingests_source_documents_without_queries(self):
        index=self.index()
        index.replace('source',self.records()+[{'id':'third','text':'Another household note'}])
        self.assertEqual(index.warm(2),2)
        self.assertEqual(index.warm(2),1)
        self.assertEqual(index.warm(2),0)
        self.assertEqual(sorted(text for call in self.client.calls for text in call),
                         sorted([record['text'] for record in self.records()]+['Another household note']))
        self.assertFalse(any(text.startswith('Instruct:') for call in self.client.calls for text in call))
        for limit in (0,81,True):
            with self.subTest(limit=limit),self.assertRaises(ValueError):index.warm(limit)

    def test_cached_semantic_neighbor_beyond_first_eighty_remains_searchable(self):
        index=self.index()
        records=[{'id':str(number),'text':'Neutral household document '+str(number)} for number in range(200)]
        records[-1]={'id':'199','text':'Gummistøvler til turen','metadata':{'position':199}}
        index.replace('source',records)
        self.assertEqual([index.warm(80),index.warm(80),index.warm(80),index.warm(80)],[80,80,40,0])
        with closing(sqlite3.connect(index.path)) as db,db:
            # Put the sole semantic neighbor after at least eighty equally
            # nonlexical passages in the deterministic source query order.
            selected=db.execute('SELECT id FROM passages WHERE text=?',(records[-1]['text'],)).fetchone()[0]
            db.execute('UPDATE passages SET id=? WHERE id=?',('z'*64,selected))
        self.client.calls.clear()
        found=index.search('waterproof boots',task='school',limit=1)
        self.assertEqual(found[0]['metadata'],{'position':199})
        self.assertEqual(found[0]['lexical_score'],0);self.assertAlmostEqual(found[0]['semantic_score'],1)
        self.assertEqual(len(self.client.calls),1);self.assertEqual(len(self.client.calls[0]),1)

    def test_source_cap_prunes_oldest_source_and_its_cached_passages(self):
        index=self.index()
        with closing(sqlite3.connect(index.path)) as db,db:
            db.executemany('INSERT INTO sources VALUES(?,?)',[(str(number),10000+number) for number in range(2000)])
            db.execute('INSERT INTO passages VALUES(?,?,?,?,?,?,?)',
                       ('0','old','Old cached source','{}','old-hash',json.dumps(basis(0)),retrieval.REVISION))
        with patch.object(retrieval.time,'time',return_value=1000):
            index.replace('current',[{'id':'current','text':'Current household document'}],ttl_seconds=31536000)
        with closing(sqlite3.connect(index.path)) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM sources').fetchone()[0],2000)
            self.assertIsNone(db.execute('SELECT id FROM sources WHERE id=?',('0',)).fetchone())
            self.assertIsNone(db.execute('SELECT id FROM passages WHERE id=?',('old',)).fetchone())
            self.assertEqual(db.execute('SELECT source FROM passages').fetchall(),[('current',)])


if __name__=='__main__':unittest.main()
