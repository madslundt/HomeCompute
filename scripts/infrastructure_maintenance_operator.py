"""Independent root review/reconciliation; never exposed on the agent socket."""
import json
import os


def operate(controller, operation, plan_id):
    if os.geteuid() != 0 or controller.uid != 0 or operation not in {'review', 'reconcile'}:
        raise ValueError('trusted root operator required')
    if operation == 'review':
        with controller.connect() as db:
            row = controller.row(db, plan_id)
            return {'receipt': controller.public(row), 'exact_plan': json.loads(row['plan'])}
    # An operator acknowledges that running state and source pins were checked.
    # Reconciliation cannot approve a new plan or retry any previous attempt.
    with controller.locked(), controller.connect() as db:
        row = controller.row(db, plan_id)
        if row['status'] not in {'succeeded', 'uncertain'}:
            raise ValueError('only a completed or uncertain attempt can be reconciled')
        db.execute("UPDATE plans SET status='reconciled',reason='operator_reconciled_live_state_and_source_pins' WHERE id=?", (plan_id,))
        return controller.public(controller.row(db, plan_id))
