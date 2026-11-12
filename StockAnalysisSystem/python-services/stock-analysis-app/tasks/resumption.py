"""One stopped-attempt/slot guard for typed manual resume; never requeues/unlocks."""
import json
from sqlalchemy import select
from .repository import tasks,slots
from .contracts import identity,Conflict,normalize_request


def parent_task(connection,id):
    identity(id)
    row=connection.execute(select(tasks).where(tasks.c.id==id)).mappings().one_or_none()
    slot=connection.execute(select(slots).where(slots.c.id==1)).mappings().one()
    if row is None or row['state'] not in ('FAILED','INTERRUPTED','CANCELLED') or slot['quarantined'] or slot['task_id']==id:
        raise Conflict('Resume requires a stopped parent and recovered execution slot')
    request=normalize_request(json.loads(row['request']))
    supported=request['kind']=='feature_build' or request['kind']=='model_workflow' and (
        (request['algorithm'],request['operation']) in (('lightgbm','predict'),('transformer','train')))
    if not supported:raise ValueError('Unsupported resume parent')
    return dict(id=id,request=request,binding=json.loads(row['binding']))


def semantic_identity(task):
    from copy import deepcopy
    from .contracts import digest
    request=normalize_request(task['request']);request.pop('resume_task_id',None)
    binding=deepcopy(task['binding']);binding.pop('resume',None)
    return digest(dict(request=request,binding=binding))


def is_lightgbm_prediction_resume(request):
    return (bool(request.get('resume_task_id')) and request.get('kind')=='model_workflow'
            and request.get('algorithm')=='lightgbm' and request.get('operation')=='predict')


def resume_calendar_path(calendar_path,binding):
    """Keep the original calendar identity after a verified append-only update."""
    import re
    from itertools import islice
    from pathlib import Path
    from .artifacts import _is_link,file_digest
    from .contracts import digest
    from .source import SourceChanged,read_calendar
    current_path=Path(calendar_path).absolute()
    expected=binding.get('data',{}).get('calendar_sha256')
    if not isinstance(expected,str) or re.fullmatch('[0-9a-f]{64}',expected) is None:
        raise SourceChanged('Missing resume calendar identity')
    if any(_is_link(path) for path in (current_path,*current_path.parents)):
        raise SourceChanged('Unsafe live calendar path')
    current=read_calendar(current_path)
    if digest(current)==expected:return current_path
    history=current_path.parent/'calendar_history'
    archive=history/f'{expected}.json'
    if _is_link(history) or _is_link(archive) or not history.is_dir():
        raise SourceChanged('Original calendar archive unavailable')
    if archive.is_file():
        original=read_calendar(archive)
        if digest(original)!=expected or file_digest(archive)!=expected:
            raise SourceChanged('Original calendar archive changed')
    else:
        # Older files are named by their raw bytes, while task binding hashes
        # the normalized calendar. Match both identities before using a file.
        candidates=list(islice(history.glob('*.json'),4097))
        if len(candidates)>4096:raise SourceChanged('Calendar archive lookup bound exceeded')
        for path in sorted(candidates):
            if re.fullmatch('[0-9a-f]{64}',path.stem) is None or _is_link(path) or not path.is_file():continue
            try:
                original=read_calendar(path)
                if digest(original)==expected and file_digest(path)==path.stem:
                    archive=path;break
            except (OSError,ValueError,KeyError,TypeError,AttributeError):continue
        else:raise SourceChanged('Original calendar archive unavailable')
    if (current['start']!=original['start']
            or current['end']<=original['end']
            or current['rows'][:len(original['rows'])]!=original['rows']):
        raise SourceChanged('Resume calendar history changed')
    return archive


def workflow_source_for_task(engine,task,calendar_path=None):
    from workflows.source import WorkflowSource
    if not is_lightgbm_prediction_resume(task['request']):return WorkflowSource(engine,calendar_path)
    from .source import SourceChanged,SourceProvider
    live=SourceProvider(engine,calendar_path).calendar_path
    selected=resume_calendar_path(live,task['binding'])

    class ResumeSource(WorkflowSource):
        def verify(self,task):
            if resume_calendar_path(live,task['binding'])!=self.calendar_path:
                raise SourceChanged('Resume calendar path changed during execution')
            return super().verify(task)

    return ResumeSource(engine,selected)
