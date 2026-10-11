"""Measured live simulator → ML/twin → scripted agents → disruption/replan check."""
import json
from app.sim.integration import IntegratedSimulator
from app.contracts.models import ScenarioEvent
from app.twin import state as twin
from app.agents.runner import run_workflow
from app.solver.store import STORE
from app.whatif.runner import replan_and_store
from app.ml import health

def main():
    simulator=IntegratedSimulator(':memory:')
    stepped=simulator.step(24)
    simulator.inject(ScenarioEvent(type='anomaly',at=simulator.state.now,tail='SIM-017',component_id='SIM-017/engine_2'))
    component=next(c for c in twin.get_twin('SIM-017').components if c.component_id=='SIM-017/engine_2')
    run=run_workflow('Investigate SIM-017 and propose maintenance',{'tails':['SIM-017']})
    assert run.status=='done',run.explanation
    assert run.work_orders,'No pending maintenance proposal was generated'
    assert STORE.current.feasible,STORE.current.infeasible_reasons
    event=ScenarioEvent(type='part_delay',at=simulator.state.now,station='DFW',part='BORESCOPE-KIT',delay_hours=36)
    simulator.inject(event)
    result=replan_and_store(event,already_applied=True)
    assert result.diff.moved or result.diff.added or result.diff.dropped,'No plan change after the disruption'
    report={'synthetic':True,'telemetry_source':simulator.source.source,'model_available':health.model_available(),'ingested':stepped['ingested'],'component_status':component.status,'rul_cycles':component.rul_cycles.model_dump() if component.rul_cycles else None,'agent_status':run.status,'pending_work_orders':len(run.work_orders),'plan_feasible':result.before.feasible,'replan_feasible':result.after.feasible,'diff':result.diff.model_dump(),'approval':'Pending human decision; no work orders automatically approved'}
    print(json.dumps(report,indent=2,default=str))

if __name__=='__main__':
    main()
