"""Synthetic live checks through the real OMH registry and isolated browser."""
import sys,json
sys.path.insert(0,'/opt/hermes')
import pm
assert not pm.check(),'sealed Hermes packages differ'
from hermes_cli.plugins import get_plugin_manager
from tools.registry import registry
m=get_plugin_manager();m.discover_and_load()
status=json.loads(registry.get_entry('omh_status').handler({'limit':1}))
assert not status.get('error'),'OMH status failed'
from tools.browser_use_cli import browser_exec
public=json.loads(browser_exec('# Checking public browsing\nnew_tab("https://example.com")\nprint(page_info())',session='homecompute-omh-check',timeout_s=30))
assert not public.get('error') and 'Example Domain' in json.dumps(public),'public browser check failed'
private=json.loads(browser_exec('# Checking private-address protection\nnew_tab("http://192.168.30.122/")',session='homecompute-omh-check',timeout_s=10))
assert private.get('error'),'private URL must be rejected'
print(json.dumps({'sealed_packages_verified':True,'omh_status_tool_called':True,'browser_public_page':'Example Domain','browser_private_url_rejected':True}))
