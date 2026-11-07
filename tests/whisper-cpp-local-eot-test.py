#!/usr/bin/env python3
"""Compile the pinned rank/core/server seams; synthetic states only, no models."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('audit_tests', ROOT/'tests/whisper-cpp-decoder-audit-test.py')
AUDIT = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(AUDIT)
PATCH = AUDIT.PATCH
LOCAL = PATCH.local_namespace

SETUP = r'''
constexpr int WHISPER_SAMPLE_RATE = 16000;
std::string captured_trace;
static void whisper_write_window_trace(const char *line, size_t n) { captured_trace.append(line,n); }
'''
MAIN = r'''
int main(int argc,char **argv) {
    if(argc!=2)return 90;
    const std::string test=argv[1];
    whisper_context context;auto *ctx=&context;
    whisper_state storage;auto *state=&storage;
    whisper_full_params params;
    const int n_decoders_cur=2,n_samples=480000;
    int best_decoder_id=0,seek=test=="seek_offset" ? 1000 : 0;
    const int seek_end=3000,delta_min=10,it=0,audit_step=4,audit_active=2;
    const bool trace_windows=std::getenv("WHISPER_CPP_TRACE_WINDOWS")!=nullptr;
    whisper_decoder_audit decoder_audits[WHISPER_MAX_DECODERS];
    bool endpoint_gate_enabled=test!="disabled";
    int32_t spans[]={0,480000,0,0};
    const int32_t *endpoint_spans=spans;
    int n_endpoint_spans=1;
    if(test=="no_spans")n_endpoint_spans=0;
    if(test=="cross_segment") { spans[1]=98304;spans[2]=110592;spans[3]=480000;n_endpoint_spans=2; }
    if(test=="old_on_start")spans[0]=81920;
    if(test=="new_on_end")spans[1]=122880;
    state->decoders[0].sequence.tokens={{1000,1000,-.05},{41,1000,-.1},{1256,1256,-.05},{999,1000,-3}};
    state->decoders[1].sequence.tokens={{1000,1000,-.05},{42,1000,-.4},{1384,1384,-.05},{999,1000,-.01}};
    for(auto &d:state->decoders){d.completed=true;d.sequence.result_len=3;}
    if(test=="same_endpoint")state->decoders[1].sequence.tokens[2].id=1256;
    if(test=="earlier_endpoint")state->decoders[1].sequence.tokens[2].id=1200;
    if(test=="old_ordinary_tail") {
        auto &s=state->decoders[0].sequence;s.tokens.insert(s.tokens.end()-1,{43,1000,-.001});s.result_len=4;
    }
    if(test=="new_ordinary_tail")state->decoders[1].sequence.tokens[2].id=43;
    if(test=="failed")state->decoders[1].failed=true;
    if(test=="unfinished")state->decoders[1].completed=false;
    if(test=="non_eot")state->decoders[1].sequence.tokens.back().id=43;
    if(test=="additional_trim")state->decoders[1].sequence.tokens.insert(state->decoders[1].sequence.tokens.end()-1,{43,1000,-.01});
    if(test=="nan_eot")state->decoders[0].sequence.tokens.back().plog=NAN;
    if(test=="invalid_timestamp")state->decoders[1].sequence.tokens[2].id=2501;
    if(test=="zero_timestamp")state->decoders[0].sequence.tokens[2].id=1000;
    if(test=="out_of_bounds") {state->decoders[1].sequence.tokens[2].id=2500;}
    if(test=="entropy_failure") {
        params.entropy_thold=.5;auto &s=state->decoders[1].sequence;
        s.tokens.assign(34,{42,1000,-.01});s.tokens.push_back({1384,1384,-.01});s.tokens.push_back({999,1000,-.01});s.result_len=35;
    }
    if(test=="tie")state->decoders[1].sequence.tokens=state->decoders[0].sequence.tokens;
    if(test=="alpha_default" || test=="alpha_zero" || test=="alpha_positive") {
        state->decoders[0].sequence.tokens={{1000,1000,-.1},{41,1000,-.3},{1256,1256,-.1},{999,1000,-.2}};
        state->decoders[1].sequence.tokens={{1000,1000,-.1},{42,1000,-.2},{43,1000,-.3},{44,1000,-.2},{1384,1384,-.1},{999,1000,-.1}};
        state->decoders[1].sequence.result_len=5;
        if(test=="alpha_zero")params.length_penalty=0;
        if(test=="alpha_positive")params.length_penalty=1;
    }
    for(auto &d:state->decoders)for(auto &t:d.sequence.tokens)d.sequence.sum_logprobs_all+=t.plog;
'''
OUTPUT = r'''
    std::printf("%d %d\n",best_decoder_id,state->n_fail_h);
    for(int j=0;j<n_decoders_cur;++j){const auto &d=state->decoders[j];
        std::printf("%d %d %d %zu %.17g %.17g %.17g %.17g %.17g %lu\n",d.completed,d.failed,d.sequence.result_len,d.sequence.tokens.size(),d.sequence.sum_logprobs_all,d.sequence.sum_logprobs,d.sequence.avg_logprobs,d.sequence.score,d.sequence.entropy,static_cast<unsigned long>(std::mt19937(d.rng)()));
        for(auto t:d.sequence.tokens)std::printf("%d %.17g ",t.id,t.plog);std::puts("");
    }
    std::fputs(captured_trace.c_str(),stderr);
}
'''
CASES = ('same_segment','disabled','no_spans','cross_segment','same_endpoint','earlier_endpoint',
         'old_on_start','new_on_end','old_ordinary_tail','new_ordinary_tail','failed','unfinished',
         'non_eot','additional_trim','nan_eot','invalid_timestamp','zero_timestamp','out_of_bounds',
         'entropy_failure','tie','seek_offset','alpha_default','alpha_zero','alpha_positive')

CORE = r'''
static int calls=0;
static int whisper_full_with_state_impl(whisper_context *,whisper_state *,whisper_full_params,const float *,int,const int32_t *,int,bool enabled){++calls;return enabled?17:19;}
'''
CORE_MAIN = r'''
int main(int argc,char **argv){
    if(argc!=2)return 90;std::string test=argv[1];
    whisper_state state;whisper_context context;context.state=&state;whisper_full_params params;
    static float samples[480000];const float *audio=samples;auto *ctx=&context;
    int count=1,n=16000;int32_t spans[]={0,15360,15872,16000};const int32_t *data=spans;
    if(test=="empty"){count=0;data=nullptr;}
    if(test=="null_spans")data=nullptr;
    if(test=="negative_count")count=-1;
    if(test=="too_many")count=33;
    if(test=="invalid_audio")n=0;
    if(test=="oversized_audio")n=480001;
    if(test=="null_audio")audio=nullptr;
    if(test=="null_context")ctx=nullptr;
    if(test=="negative_start")spans[0]=-512;
    if(test=="reversed")spans[1]=0;
    if(test=="out_of_bounds")spans[1]=16384;
    if(test=="offgrid_start")spans[0]=1;
    if(test=="offgrid_end")spans[1]=15361;
    if(test=="overlap"){count=2;spans[2]=14848;}
    if(test=="touch"){count=2;spans[2]=15360;}
    if(test=="partial_final"){spans[1]=16000;}
    if(test=="no_timestamps")params.no_timestamps=true;
    if(test=="vad")params.vad=true;
    if(test=="detect")params.detect_language=true;
    if(test=="offset")params.offset_ms=1;
    if(test=="duration")params.duration_ms=10;
    const int result=test=="old_API"?whisper_full_with_state(ctx,&state,params,audio,n):whisper_full_with_endpoint_spans(ctx,params,audio,n,data,count);
    std::printf("%d %d\n",result,calls);
}
'''
SERVER_MAIN = r'''
int main(int argc,char **argv){if(argc!=3)return 90;std::vector<int32_t> out={123};
    const bool valid=parse_endpoint_spans(argv[1],std::stoi(argv[2]),out);std::printf("%d",valid);
    for(auto x:out)std::printf(" %d",x);std::puts("");}
'''

SERVER_STUBS = r"""
#include <map>
struct Value { std::string content; };
struct Request {
    std::multimap<std::string,Value> files;
    bool has_file(const char *name) const {return files.count(name)>0;}
    const Value &get_file_value(const char *name) const {return files.find(name)->second;}
};
struct Response {int status=200;void set_content(const std::string &,const char *){}};
struct Params {int n_processors=1;};
struct FullParams {bool no_timestamps=false,vad=false,detect_language=false;int offset_ms=0,duration_ms=0;};
int legacy_calls=0,endpoint_calls=0;
int whisper_full_parallel(void *,FullParams,const float *,int,int){++legacy_calls;return 0;}
int whisper_full_with_endpoint_spans(void *,FullParams,const float *,int,const int32_t *,int){++endpoint_calls;return 0;}
void handle(Request &req,Response &res,Params params,FullParams wparams) {
    void *ctx=nullptr;std::vector<float> pcmf32(16000);
"""
SERVER_PROBE = r"""
        res.status=500;return;
    }
}
int main(int argc,char **argv){
 if(argc!=2)return 90;const std::string test=argv[1];Request req;Response res;Params params;FullParams wparams;
 if(test!="absent")req.files.emplace("homecompute_eot_spans",Value{test=="empty"?"":"0:512"});
 if(test=="duplicate")req.files.emplace("homecompute_eot_spans",Value{"0:512"});
 if(test=="malformed")req.files.begin()->second.content="0:NaN";
 if(test=="parallel")params.n_processors=2;
 if(test=="no_timestamps")wparams.no_timestamps=true;
 if(test=="vad")wparams.vad=true;
 if(test=="detect")wparams.detect_language=true;
 if(test=="offset")wparams.offset_ms=1;
 if(test=="duration")wparams.duration_ms=1;
 handle(req,res,params,wparams);std::printf("%d %d %d\n",res.status,legacy_calls,endpoint_calls);
}
"""

class LocalRankingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp=tempfile.TemporaryDirectory();cls.addClassCleanup(cls.tmp.cleanup);cls.directory=Path(cls.tmp.name)
        cls.compiler=shutil.which('c++')
        if cls.compiler is None:raise RuntimeError('Local EOT regression requires C++17')
        cls.original=AUDIT.WINDOW.read_text()+AUDIT.FIXTURE.read_text()
        cls.programs={}
        for name,text in [('original',cls.original),('ranked',LOCAL['patch_rank'](cls.original)),
                          ('traced',LOCAL['patch_rank'](PATCH.patch_decoder_trace(PATCH.patch_window_trace(cls.original))))]:
            code=AUDIT.PREAMBLE+SETUP+PATCH.trace_namespace['DECODER_HELPERS']+LOCAL['NATIVE_HELPERS']
            code+=AUDIT.between(text,'SCORE')+MAIN+AUDIT.between(text,'RANK')+OUTPUT
            cls.build(name,code)
        core_preamble=AUDIT.PREAMBLE.replace('struct whisper_context { struct { int n_loaded = 1; } model; };',
            'struct whisper_state; struct whisper_context { whisper_state *state=nullptr; struct { int n_loaded=1; } model; };')
        core_preamble=core_preamble.replace('int max_tokens = 0;','int max_tokens = 0; bool vad=false,detect_language=false; int offset_ms=0,duration_ms=0;')
        cls.build('core',core_preamble+SETUP+LOCAL['NATIVE_HELPERS']+CORE+LOCAL['WRAPPERS']+CORE_MAIN)
        prefix='#include <string>\n#include <vector>\n#include <cstdint>\n#include <cstdio>\n'
        cls.build('parser',prefix+LOCAL['SERVER_PARSER']+SERVER_MAIN)
        cls.build('server',prefix+LOCAL['SERVER_PARSER']+SERVER_STUBS+LOCAL['SERVER_LOCAL']+SERVER_PROBE)

    @classmethod
    def build(cls,name,code):
        cpp=cls.directory/(name+'.cpp');cpp.write_text(code);binary=cls.directory/name
        r=subprocess.run([cls.compiler,'-std=c++17','-O2',str(cpp),'-o',str(binary)],capture_output=True,text=True,timeout=60)
        if r.returncode:raise AssertionError(r.stderr)
        cls.programs[name]=binary

    def run_case(self,case,variant='ranked',trace=False):
        env=dict(os.environ);env.pop('WHISPER_CPP_TRACE_WINDOWS',None)
        if trace:env['WHISPER_CPP_TRACE_WINDOWS']='1'
        result=subprocess.run([self.programs[variant],case],env=env,capture_output=True,text=True,check=True,timeout=5)
        return result.stdout.splitlines(),[json.loads(x) for x in result.stderr.splitlines()]

    def test_single_same_segment_later_completion_changes_only_winner(self):
        old,_=self.run_case('same_segment','original');new,_=self.run_case('same_segment')
        self.assertEqual(old[0],'0 0');self.assertEqual(new[0],'1 0');self.assertEqual(old[1:],new[1:])

    def test_disabled_and_every_unknown_or_nonlocal_endpoint_keep_v6(self):
        unchanged=set(CASES)-{'same_segment','seek_offset','alpha_default','alpha_zero'}
        for case in unchanged:
            with self.subTest(case=case):self.assertEqual(self.run_case(case)[0],self.run_case(case,'original')[0])

    def test_no_decoder_state_confidence_entropy_token_or_rng_change(self):
        for case in CASES:
            with self.subTest(case=case):self.assertEqual(self.run_case(case)[0][1:],self.run_case(case,'original')[0][1:])

    def test_typed_trace_is_observational_and_does_not_change_v2_schema(self):
        for case in CASES:
            with self.subTest(case=case):
                plain,_=self.run_case(case);traced,rows=self.run_case(case,'traced',True)
                self.assertEqual(plain,traced)
                if case=='disabled':self.assertEqual(rows,[])
                else:
                    self.assertEqual(len(rows),1);self.assertEqual(rows[0]['version'],3);self.assertEqual(rows[0]['event'],'local_eot_rank')
        _,rows=self.run_case('seek_offset','traced',True)
        self.assertEqual(rows[0]['legacy_endpoint_sample'],241920);self.assertEqual(rows[0]['complete_endpoint_sample'],282880)
        self.assertTrue(rows[0]['same_speech_segment']);self.assertTrue(rows[0]['strictly_later'])
        _,rows=self.run_case('same_endpoint','traced',True)
        self.assertTrue(rows[0]['same_speech_segment']);self.assertFalse(rows[0]['strictly_later'])
        self.assertEqual(rows[0]['chosen_decoder'],rows[0]['legacy_decoder'])

    def test_native_length_penalty_and_strict_tie_semantics_are_preserved(self):
        for case,winner in [('alpha_default',1),('alpha_zero',1),('alpha_positive',0),('tie',0)]:
            self.assertTrue(self.run_case(case)[0][0].startswith(str(winner)+' '))

    def test_core_validates_all_internal_params_before_inference(self):
        valid={'valid','empty','partial_final'}
        cases=valid|{'null_spans','negative_count','too_many','invalid_audio','oversized_audio','null_audio',
                     'null_context','negative_start','reversed','out_of_bounds','offgrid_start','offgrid_end',
                     'overlap','touch','no_timestamps','vad','detect','offset','duration'}
        for case in cases:
            with self.subTest(case=case):
                result=subprocess.check_output([self.programs['core'],case],text=True).strip()
                self.assertEqual(result,'17 1' if case in valid else '-12 0')
        self.assertEqual(subprocess.check_output([self.programs['core'],'old_API'],text=True).strip(),'19 1')

    def test_actual_server_parser_checks_shape_size_types_grid_order_and_bounds(self):
        valid={'':[], '0:512':[0,512], '0:15360;15872:16000':[0,15360,15872,16000]}
        for raw,points in valid.items():
            r=subprocess.check_output([self.programs['parser'],raw,'16000'],text=True).strip()
            self.assertEqual(r,' '.join(map(str,[1,*points])))
        invalid=['-1:512','0.0:512','0:NaN','0:512;','0:512;;1024:1536','true','[]','1:512',
                 '0:513','0:16001','0:512;512:1024','1024:1536;0:512','0:999999999999999999999999999','x'*32769]
        for raw in invalid:
            with self.subTest(raw=raw[:60]):
                self.assertEqual(subprocess.check_output([self.programs['parser'],raw,'16000'],text=True).strip(),'0 123')

    def test_actual_server_dispatch_refuses_invalid_internal_metadata_before_decode(self):
        for case in ('absent','valid','empty','duplicate','malformed','parallel','no_timestamps','vad','detect','offset','duration'):
            with self.subTest(case=case):
                output=subprocess.check_output([self.programs['server'],case],text=True).strip()
                self.assertEqual(output,'200 1 0' if case=='absent' else '200 0 1' if case in ('valid','empty') else '400 0 0')

    def test_patch_anchors_fail_closed_and_old_parameter_struct_is_not_extended(self):
        for old,new in LOCAL['RANK_PATCHES']:
            for text in [self.original.replace(old,'',1),self.original+old]:
                with self.assertRaises(ValueError):LOCAL['patch_rank'](text)
        header=LOCAL['FILE_PATCHES']['include/whisper.h']
        self.assertEqual(len(header),1);self.assertNotIn('struct whisper_full_params {',header[0][1])

if __name__=='__main__':unittest.main(verbosity=2)
