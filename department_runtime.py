"""Existing-turn collaboration CLI/shared tools; no model or background Notion API."""
import argparse
from contextlib import closing
import json
from pathlib import Path
import sqlite3

ROOT=Path(__file__).resolve().parent
REVISION='2026-11-04-team-handoff-v2'


def status(db):
    import department_journal,department_work
    notion=department_journal.snapshot(db)
    with closing(sqlite3.connect(Path(db).resolve().as_uri()+'?mode=ro',uri=True,timeout=5)) as c:
        rows=[json.loads(r[0]) for r in c.execute('SELECT data FROM office_department_tasks')]
    return {'revision':REVISION,'registered_threads':len(department_work.registered(db)),
            'observer_threads_in_this_process':len(department_work._observer_status['observed_threads']),
            'observer_errors_in_this_process':department_work._observer_status['errors'],
            'tasks':len(rows),'direct':sum(r.get('origin')=='direct' for r in rows),
            'assigned':sum(r.get('origin')!='direct' for r in rows),'inbox_pending':department_work.inbox(db)['pending'],
            'notion_synced':sum(r['sync_state']=='synced' for r in notion['items']),
            'notion_pending':notion['pending_count'],'notion_transport':notion['transport'],
            'business_validation':'Native final/source loaded/Notion readback are distinct from actual business/UI success.'}


def observe_actual(db,task_id):
    import department_knowledge
    found=department_knowledge.search(db,task_id,limit=1)
    return dict(status(db),queried_task_id=task_id,query_matches=found['total'],index_revision=found['index_revision'],
                notion_references=[r['notion_url'] for r in found['items'] if r.get('notion_url')],
                observation='Actual saved work and code lookup; no business rerun, fabricated input, or Notion network write.')


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--db',default=str(ROOT/'office.db'))
    commands=p.add_subparsers(dest='command',required=True)
    commands.add_parser('status');commands.add_parser('inbox')
    doc=commands.add_parser('read-document');doc.add_argument('--id',required=True);doc.add_argument('--offset',type=int,default=0);doc.add_argument('--max-chars',type=int,default=5000)
    search=commands.add_parser('search');search.add_argument('--query',default='');search.add_argument('--team-id',default='');search.add_argument('--project-id',default='');search.add_argument('--limit',type=int,default=5)
    route=commands.add_parser('route');route.add_argument('--query',required=True);route.add_argument('--context',default='');route.add_argument('--project-id',default='')
    assign=commands.add_parser('assign');assign.add_argument('--request-id',required=True);assign.add_argument('--thread-id',required=True);assign.add_argument('--title',required=True);assign.add_argument('--preserve',required=True);assign.add_argument('--completion',required=True);assign.add_argument('--context',default='');assign.add_argument('--project-id',default='')
    assign.add_argument('--depends-on',action='append',default=[])
    accept=commands.add_parser('accept-handoff');accept.add_argument('--request-id',required=True);accept.add_argument('--assignment-id',required=True);accept.add_argument('--result-sha256',required=True);accept.add_argument('--evidence',required=True)
    declare=commands.add_parser('declare');declare.add_argument('--request-id',required=True);declare.add_argument('--title',required=True);declare.add_argument('--kind',required=True)
    export=commands.add_parser('export-journal');export.add_argument('--task-id',default='');export.add_argument('--limit',type=int,default=20)
    ack=commands.add_parser('ack-journal');ack.add_argument('--receipt-file',required=True)
    args=p.parse_args();db=Path(args.db)
    import department_knowledge as knowledge,department_work as work,department_journal as journal
    if args.command=='read-document':result=__import__('notion_memory').read_document(args.id,args.offset,args.max_chars)
    elif args.command=='status':result=status(db)
    elif args.command=='inbox':result=work.inbox(db)
    elif args.command=='search':result=knowledge.search(db,args.query,args.team_id,args.project_id,args.limit)
    elif args.command=='route':result=knowledge.route(db,args.query,args.context,args.project_id)
    elif args.command=='assign':result=knowledge.assign_route(db,args.request_id,args.thread_id,args.title,args.preserve,args.completion,args.context,args.project_id,args.depends_on)
    elif args.command=='accept-handoff':
        import department_tasks
        result=department_tasks.accept_handoff(db,args.request_id,args.assignment_id,args.result_sha256,args.evidence)
    elif args.command=='declare':result=work.declare_current(db,args.request_id,args.title,args.kind)
    elif args.command=='export-journal':result=journal.pending(db,args.task_id,args.limit)
    else:result=journal.acknowledge(db,json.loads(Path(args.receipt_file).read_text(encoding='utf-8-sig')))
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
