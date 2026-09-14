"""Read-only idle check in a bounded subprocess; never start a solver."""
import json,os,sys,psutil
def main():
    processes=list(psutil.process_iter(['pid','name']))
    solvers=[p.info for p in processes if 'solver_hf' in (p.info['name'] or '').lower()]
    if solvers:
        print(json.dumps(dict(safe_to_continue=False,reason='solver_process_present',processes=solvers)));return
    environments=[p.info for p in processes if 'cst design environment' in (p.info['name'] or '').lower()]
    if not environments:
        print(json.dumps(dict(safe_to_continue=True,reason='no_CST_environment_or_HF_solver_process')));return
    libraries=os.environ.get('CST_PYTHON_LIBRARIES')
    if not libraries:raise RuntimeError('CST_PYTHON_LIBRARIES is not configured')
    sys.path.insert(0,libraries)
    import cst.interface as ci
    env=ci.DesignEnvironment.connect_to_any_or_new()
    running=[bool(p.model3d.is_solver_running(timeout=10)) for p in env.get_open_projects()]
    # Recheck native process state after API calls to detect transitions.
    live=[p.info for p in psutil.process_iter(['pid','name']) if 'solver_hf' in (p.info['name'] or '').lower()]
    print(json.dumps(dict(safe_to_continue=not any(running) and not live,
                         reason='CST_API_and_native_process_check',project_solver_states=running,processes=live)))
if __name__=='__main__':main()
