"""Inspectable patches for the existing household briefing workflows."""
from pathlib import Path
from typing import Any

DIRECTORY = Path(__file__).resolve().parents[1] / 'automations/household-routines'
TOMORROW_SETTINGS = {'timezone': 'Europe/Copenhagen', 'executionTimeout': 60,
                     'saveDataSuccessExecution': 'none', 'saveDataErrorExecution': 'none',
                     'saveManualExecutions': False}


def morning_patch(workflow: dict[str, Any]) -> list[dict[str, Any]]:
    names = {node['name']: node for node in workflow['nodes']}
    if not {'Get morning reminders', 'Build morning overview', 'Get today calendar'} <= names.keys():
        raise ValueError('Existing morning briefing graph required')
    operations = []
    if 'Get shared family tasks' not in names:
        operations += [
            {'type': 'addNode', 'node': {
                'name': 'Get shared family tasks', 'type': 'n8n-nodes-base.homeAssistant', 'typeVersion': 1,
                'position': [2040, 240], 'credentials': names['Get today calendar']['credentials'],
                'parameters': {'resource': 'state', 'operation': 'get', 'entityId': 'sensor.homecompute_family_tasks'}}},
            {'type': 'removeConnection', 'source': 'Get morning reminders', 'target': 'Build morning overview'},
            {'type': 'addConnection', 'source': 'Get morning reminders', 'target': 'Get shared family tasks'},
            {'type': 'addConnection', 'source': 'Get shared family tasks', 'target': 'Build morning overview'},
        ]
    operations += [
        {'type': 'setNodeSettings', 'nodeName': 'Get shared family tasks',
         'settings': {'onError': 'continueRegularOutput', 'executeOnce': True}},
        {'type': 'setNodeSettings', 'nodeName': 'Get Home daytime forecast',
         'settings': {'onError': 'continueRegularOutput'}},
        {'type': 'updateNodeParameters', 'nodeName': 'Build morning overview',
         'parameters': {'mode': 'runOnceForAllItems', 'jsCode': (DIRECTORY / 'morning-overview.js').read_text()}},
        {'type': 'setNodeParameter', 'nodeName': 'Mirror morning overview to Mattermost',
         'path': 'bodyParameters.parameters', 'value': [
             {'name': 'channel_id', 'value': next(row['value'] for row in
              names['Mirror morning overview to Mattermost']['parameters']['bodyParameters']['parameters']
              if row['name'] == 'channel_id')},
             {'name': 'message', 'value': "={{ $('Build morning overview').first().json.home_message }}"}]},
    ]
    return operations


def school_evening_patch(workflow: dict[str, Any]) -> list[dict[str, Any]]:
    node = next(row for row in workflow['nodes'] if row['name'] == 'Prepare channel deliveries')
    original = "if(collector.status!=='error'&&['AFTERNOON','EVENING','WEEK_CLOSE','WEEK_PREP'].includes(ctx.mode)){"
    updated = original.replace("&&['AFTERNOON'", "&&ctx.mode!=='EVENING'&&['AFTERNOON'")
    source = node['parameters']['jsCode']
    if updated in source:
        return []
    if source.count(original) != 1:
        raise ValueError('Review changed Aula delivery logic before replacing evening delivery')
    return [{'type': 'updateNodeParameters', 'nodeName': node['name'],
             'parameters': {**node['parameters'], 'jsCode': source.replace(original, updated)}}]
