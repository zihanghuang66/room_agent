"""One wire schema shared by Codex structured output and plan validation."""
PLAN_ARRAY_FIELDS=('objects','preserve','steps','expected_outputs','target_ids','finding_ids')
BINDING_FIELDS=frozenset(('target_ids','finding_ids'))
PLAN_PROPERTIES={
    'target_run_id':{'type':'string'},'change':{'type':'string'},
    **{key:{'type':'array','items':{'type':'string'}} for key in PLAN_ARRAY_FIELDS},
}
LEGACY_PLAN_FIELDS=frozenset(PLAN_PROPERTIES)
WORKFLOW_FIELDS=frozenset(('operation','operation_run_id'))
PLAN_PROPERTIES.update(operation={'type':'string','enum':['scene_edit','scene_review','resume_operation']},operation_run_id={'type':'string'})
PLAN_FIELDS=frozenset(PLAN_PROPERTIES)
OUTPUT_SCHEMA={
    'type':'object','additionalProperties':False,'required':['kind','reply','questions','plan'],
    'properties':{
        'kind':{'type':'string','enum':['answer','clarify','plan','unsupported']},
        'reply':{'type':'string'},'questions':{'type':'array','items':{'type':'string'}},
        'plan':{'anyOf':[{'type':'null'},{'type':'object','additionalProperties':False,
                'required':list(PLAN_PROPERTIES),'properties':PLAN_PROPERTIES}]},
    },
}

# A host-mediated read request, never a filesystem path or executable command.
INSPECTION_SCHEMA={
    'type':'object','additionalProperties':False,'required':['files','queries','images'],
    'properties':{
        'files':{'type':'array','items':{'type':'object','additionalProperties':False,
            'required':['id','start','lines'],'properties':{'id':{'type':'string'},'start':{'type':'integer'},'lines':{'type':'integer'}}}},
        'queries':{'type':'array','items':{'type':'string'}},
        'images':{'type':'array','items':{'type':'string'}},
    },
}
OUTPUT_SCHEMA['required']+=['inspection','citations']
OUTPUT_SCHEMA['properties']['kind']['enum'].append('inspect')
OUTPUT_SCHEMA['properties']['inspection']={'anyOf':[{'type':'null'},INSPECTION_SCHEMA]}
OUTPUT_SCHEMA['properties']['citations']={'type':'array','items':{'type':'string'}}


def bound_schema(facts):
    """Constrain routing identifiers using host state, never prose parsing."""
    from copy import deepcopy
    from room_agent.storage import identifier
    schema=deepcopy(OUTPUT_SCHEMA)
    fields=next(row for row in schema['properties']['plan']['anyOf'] if row['type']=='object')['properties']
    fields['target_run_id']['enum']=[identifier(facts['run_id'])]
    actions=facts.get('workflow_actions',{})
    operations=['scene_edit'];targets=['']
    if actions.get('scene_review'):operations.append('scene_review')
    if actions.get('resume_operation'):
        operations.append('resume_operation');targets.append(identifier(actions['resume_operation']['run_id']))
    fields['operation']['enum']=operations
    fields['operation_run_id']['enum']=targets
    return schema
