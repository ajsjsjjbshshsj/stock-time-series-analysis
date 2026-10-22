"""Owned intent authority for known DDL recovery, never an arbitrary repair API."""
from automation.repository import ActivationJournal,intent


class ManagedPublication:
    def __init__(self,engine,value,owner_guard,*,recovery_ids=()):
        self.intent=intent(value);self.owner_guard=owner_guard
        self.journal=ActivationJournal(engine);self.recovery_ids=tuple(recovery_ids)

    def require_previous(self,previous):
        actual=previous['build_id'] if previous else None
        if actual!=self.intent['previous_id']:raise ValueError('Active feature version changed')

    def prepare(self):self.journal.prepare(self.intent)
    def finish(self,state):self.journal.finish(self.intent,state)

    def recover(self,connection,incoming):
        from database import feature_store_publication as pub
        from data_processor.feature_store_contract import CANONICAL_TABLE
        pending=self.journal.pending()
        if {row['task_id'] for row in pending}!=set(self.recovery_ids):
            raise ValueError('Unowned publication recovery state')
        for row in pending:
            if any(row[key]!=self.intent[key] for key in ('build_id','previous_id','action')):
                raise ValueError('Publication recovery binding changed')
            self.owner_guard()
            active=pub.active_metadata(connection);connection.commit()
            candidate=row['build_id']
            if active and active['build_id']==candidate and active['state']=='READY':
                pub.validate_table(connection,CANONICAL_TABLE,candidate)
                self.journal.finish(row,'COMMITTED');continue
            if (active['build_id'] if active else None)!=row['previous_id']:
                raise ValueError('Unknown active publication recovery')
            if pub.inspect(connection).has_table(incoming):
                pub.validate_table(connection,incoming,candidate)
                if active:pub.validate_table(connection,CANONICAL_TABLE,active['build_id'])
                elif pub.inspect(connection).has_table(CANONICAL_TABLE):raise ValueError('Unknown canonical feature table')
            else:
                # Prove both identities before restoring anything. Original
                # process shutdown is enforced by the task-slot quarantine.
                self.owner_guard()
                pub.restore_pair(connection,incoming,active,candidate)
            self.journal.finish(row,'ABORTED')
