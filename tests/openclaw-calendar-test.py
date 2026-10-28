"""Calendar boundaries, semantic readback and durable mocked writes; no Google calls."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import openclaw_calendar as calendar
from openclaw_calendar_events import Events, body, matches, project, times, writable
from openclaw_household_requests import Requests

REQUEST = '00000000-0000-4000-8000-000000000001'
ARGS = {'calendar': 'family', 'summary': 'Synthetic', 'start': '2026-10-24T14:00:00+02:00',
        'end': '2026-10-24T15:00:00+02:00', 'all_day': False, 'request_id': REQUEST}
EVENT = {'id': 'synthetic', 'etag': '"etag-one"', 'status': 'confirmed', 'organizer': {'self': True}, **body(ARGS)}


class CalendarTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state = Path(self.tmp.name)
        self.state.chmod(0o700)
        self.ledger = Requests(self.state / 'calendar-requests.sqlite3')

    def facade(self, bridge):
        facade = calendar.Calendar.__new__(calendar.Calendar)
        facade._bridge = bridge
        facade.receipts = self.ledger
        return facade

    def test_schema_requires_calendar_and_refuses_invitations_work_delete_and_arbitrary_fields(self):
        invalid = [('create', {k:v for k,v in ARGS.items() if k != 'calendar'}),
                   ('create', {**ARGS, 'calendar': 'work'}), ('create', {**ARGS, 'attendees': []}),
                   ('create', {**ARGS, 'sendUpdates': 'all'}), ('create', {**ARGS, 'all_day': 1}),
                   ('create', {**ARGS, 'request_id': 'new'}), ('delete', {'calendar': 'family'}),
                   ('update', {'calendar': 'family', 'event_id': 'synthetic', 'request_id': REQUEST,
                               'expected_etag': 'etag-one', 'summary': 'Changed'}),
                   ('events', {'calendar': 'family', 'event_id': 'synthetic', 'query': 'x'}),
                   ('availability', {'calendars': 'family,family', 'start': ARGS['start'], 'end': ARGS['end']})]
        for op, args in invalid:
            with self.subTest(op=op), self.assertRaises(ValueError):
                calendar.validate(calendar.PREFIX+op, args)
        self.assertEqual(calendar.validate(calendar.PREFIX+'create', ARGS), ARGS)
        self.assertEqual(len(calendar.TOOLS), 6)

    def test_dst_gap_fold_all_day_and_bounds(self):
        with self.assertRaises(ValueError):
            times('2026-03-29T02:30:00+01:00', '2026-03-29T04:00:00+02:00', False)
        self.assertTrue(times('2026-10-25T02:30:00+02:00', '2026-10-25T02:30:00+01:00', False))
        self.assertEqual(times('2026-10-24', '2026-10-25', True)['end'], {'date':'2026-10-25'})
        for start, end, all_day in [('2026-02-30','2026-03-01',True),('2026-10-24','2026-10-24',True),
                                    ('2026-10-24','2026-12-24',True),('2026-10-24T14:00:00','2026-10-24T15:00:00',False)]:
            with self.assertRaises(ValueError):
                times(start,end,all_day)

    def test_equivalent_provider_offsets_and_missing_timezone_are_confirmed(self):
        event = copy.deepcopy(EVENT)
        event['start'] = {'dateTime': '2026-10-24T12:00:00Z'}
        event['end'] = {'dateTime': '2026-10-24T13:00:00+00:00'}
        self.assertTrue(matches(event, body(ARGS)))
        event['end']['dateTime'] = '2026-10-24T13:01:00Z'
        self.assertFalse(matches(event, body(ARGS)))

    def test_write_once_then_readback_and_restart_replay(self):
        created = {**copy.deepcopy(EVENT), 'id':'hc'+REQUEST.replace('-',''),
                   'extendedProperties': {'private': {'homecompute_request':REQUEST}}}
        bridge = Mock(side_effect=[{'event':None}, {'status':201}, {'event':created}])
        result = self.facade(bridge).request(calendar.PREFIX+'create', ARGS)
        self.assertEqual(result['outcome'], 'confirmed')
        self.assertTrue(result['changed'])
        self.facade(bridge).request(calendar.PREFIX+'create', ARGS)
        self.assertEqual(bridge.call_count,3)
        self.assertEqual([x.args[0] for x in bridge.call_args_list], ['event','create','event'])
        with self.assertRaises(ValueError):
            self.facade(bridge).request(calendar.PREFIX+'create', {**ARGS,'summary':'different'})

    def test_uncertain_timeout_is_fenced_and_contains_no_private_failure(self):
        bridge = Mock(side_effect=[{'event':None}, ValueError('PRIVATE OAuth response')])
        facade = self.facade(bridge)
        result = facade.request(calendar.PREFIX+'create', ARGS)
        self.assertEqual(result['outcome'],'uncertain')
        self.assertNotIn('PRIVATE',json.dumps(result))
        self.assertTrue(facade.request(calendar.PREFIX+'create',ARGS)['replayed'])
        self.assertEqual(bridge.call_count,2)
        self.assertEqual(facade.request(calendar.PREFIX+'request_status',{'request_id':REQUEST})['state'],'uncertain')

    def test_google_412_after_valid_preflight_is_fenced_without_reapply_or_readback(self):
        args={'calendar':'family','event_id':'synthetic','expected_etag':'"etag-one"','summary':'Updated','request_id':REQUEST}
        bridge=Mock(side_effect=[{'event':EVENT},{'status':412}])
        facade=self.facade(bridge)
        self.assertEqual(facade.request(calendar.PREFIX+'update',args)['outcome'],'uncertain')
        self.assertTrue(facade.request(calendar.PREFIX+'update',args)['replayed'])
        self.assertEqual(bridge.call_count,2)

    def test_update_stale_guest_foreign_recurring_special_managed_never_send(self):
        args = {'calendar':'family','event_id':'synthetic','expected_etag':'"etag-one"','summary':'Updated','request_id':REQUEST}
        variants = [{'etag':'"new"'}, {'attendees':[{'email':'PRIVATE'}]}, {'attendeesOmitted':True},
                    {'organizer':{'self':False}}, {'recurringEventId':'parent'}, {'recurrence':['RRULE:x']},
                    {'eventType':'focusTime'}, {'extendedProperties':{'private':{'sourceType':'aula'}}}]
        for changed in variants:
            client = Mock(return_value={'event':{**EVENT,**changed}})
            before=Mock()
            with self.subTest(changed=list(changed)):
                if 'extendedProperties' in changed:
                    result = Events(client).write('update',args,before)
                    self.assertEqual(result['blocked_reason'],'source_managed_event')
                else:
                    with self.assertRaises(ValueError):
                        Events(client).write('update',args,before)
                before.assert_not_called()
                self.assertEqual(client.call_count,1)

    def test_patch_preserves_unknown_metadata_and_exact_quoted_etag(self):
        existing = {**EVENT,'extendedProperties':{'private':{'unrelated':'keep'}},'colorId':'3'}
        updated = {**existing,'summary':'Updated','etag':'"two"'}
        args={'calendar':'family','event_id':'synthetic','expected_etag':'"etag-one"','summary':'Updated','request_id':REQUEST}
        bridge=Mock(side_effect=[{'event':existing},{'status':200},{'event':updated}])
        result=self.facade(bridge).request(calendar.PREFIX+'update',args)
        self.assertEqual(result['outcome'],'confirmed')
        self.assertEqual(bridge.call_args_list[1].args[1],args)

    def test_malformed_nested_provider_data_yields_blocked_or_uncertain_receipt(self):
        for index,(key,value) in enumerate([('start',[]),('organizer',None),('extendedProperties',{'private':[]}),('attendees',{})],2):
            with self.subTest(key=key):
                client=Mock(return_value={'event':{**EVENT,key:value}})
                result=self.facade(client).request(calendar.PREFIX+'create',{**ARGS,'request_id':REQUEST[:-1]+str(index)})
                self.assertEqual(result['outcome'],'blocked')
        # A malformed readback occurs after sending and must become uncertain.
        client=Mock(side_effect=[{'event':None},{'status':201},{'event':{**EVENT,'start':[]}}])
        self.assertEqual(self.facade(client).request(calendar.PREFIX+'create',ARGS)['outcome'],'uncertain')

    def test_missing_or_foreign_creation_marker_is_not_adopted(self):
        before=Mock()
        with self.assertRaises(ValueError):
            Events(Mock(return_value={'event':EVENT})).write('create',ARGS,before)
        before.assert_not_called()

    def test_read_projection_hides_emails_private_ids_and_extended_properties(self):
        raw={**EVENT,'attendees':[{'email':'PRIVATE'}], 'creator':{'email':'PRIVATE'},
             'extendedProperties':{'private':{'secret':'PRIVATE'}}}
        result=self.facade(Mock(return_value={'event':raw})).request(calendar.PREFIX+'events',{'calendar':'family','event_id':'synthetic'})
        self.assertNotIn('PRIVATE',json.dumps(result))
        self.assertFalse(result['events'][0]['writable'])

    def test_pagination_and_unknown_availability_stay_explicit(self):
        facade=self.facade(Mock(return_value={'events':[],'truncated':True}))
        result=facade.request(calendar.PREFIX+'events',{'calendar':'family','start':ARGS['start'],'end':ARGS['end']})
        self.assertTrue(result['truncated'])
        facade._bridge=Mock(return_value={'calendars':[{'alias':'family','status':'unknown','busy':[]}]})
        result=facade.request(calendar.PREFIX+'availability',{'calendars':'family','start':ARGS['start'],'end':ARGS['end']})
        self.assertEqual(result['calendars'][0]['status'],'unknown')
        facade._bridge=Mock(return_value={'calendars':[{'alias':'family','status':'known','busy':[
            {'start':'2026-10-24T12:00:00.000+00:00','end':'2026-10-24T13:00:00.000+00:00'}]}]})
        self.assertEqual(len(facade.request(calendar.PREFIX+'availability',{'calendars':'family','start':ARGS['start'],'end':ARGS['end']})['calendars'][0]['busy']),1)
        for malformed in [[None], [{'alias':'family','status':'known','busy':[None]}]]:
            facade._bridge=Mock(return_value={'calendars':malformed})
            with self.assertRaises(ValueError):
                facade.request(calendar.PREFIX+'availability',{'calendars':'family','start':ARGS['start'],'end':ARGS['end']})

    def test_config_and_transport_are_fixed_private_and_sanitized(self):
        with patch.object(calendar,'private',side_effect=[json.dumps({'schema_version':1,'bridge_token_file':str(calendar.TOKEN_PATH)}).encode(),b'a'*64]):
            facade=calendar.Calendar(self.state/'config',self.state)
        opener=Mock(); opener.open.side_effect=OSError('PRIVATE token')
        with patch.object(calendar.urllib.request,'build_opener',return_value=opener),self.assertRaisesRegex(ValueError,'^Calendar bridge unavailable$'):
            facade._bridge('list',{})
        request=opener.open.call_args.args[0]
        self.assertEqual(request.full_url,calendar.ENDPOINT)
        self.assertEqual(request.method,'POST')
        with patch.object(calendar,'private',return_value=b'{"schema_version":1,"bridge_token_file":"/tmp/other"}'),self.assertRaises(ValueError):
            calendar.Calendar(self.state/'config',self.state)


if __name__ == '__main__':
    unittest.main()
