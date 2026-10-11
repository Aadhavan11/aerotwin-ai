"""Run a reproducible simulator smoke check without a server."""
import json
from app.contracts.models import ScenarioEvent
from app.sim.fleet import dump_fixtures
from app.sim.service import Simulator

def main():
    simulator=Simulator(':memory:')
    dump_fixtures(simulator.state)
    step=simulator.step(24)
    fault=simulator.inject(ScenarioEvent(type='anomaly',at=simulator.state.now,tail='SIM-017',component_id='SIM-017/engine_2'))
    delay=simulator.inject(ScenarioEvent(type='part_delay',at=simulator.state.now,station='DFW',part='BORESCOPE-KIT',delay_hours=12))
    assert step['ingested']==576
    assert fault['event_id']!=delay['event_id']
    print(json.dumps({'step':step,'fault':fault,'delay':delay},default=str,indent=2))

if __name__=='__main__':
    main()
