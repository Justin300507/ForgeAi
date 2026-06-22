import sys
sys.path.insert(0, '.')
from app.services.v11_orchestrator import generate_project_v11

print('=== V11 SINGLE APP TEST ===')
result = generate_project_v11(
    idea='A job board with job listings, employers, and applicants',
    provider='auto',
    deploy_provider='railway',
    run_improvement_cycle=False,
    skip_reviews=True,
)

v11        = result.get('v11_extras', {})
deployment = v11.get('deployment', {})
health     = v11.get('health_report', {})
forge      = result.get('forge_score', {})
runtime    = result.get('runtime') or {}

print()
print('=== RESULTS ===')
print('Project      :', result.get('project_name'))
print('Static pass  :', bool((result.get('validation') or {}).get('passed')))
print('Runtime pass :', bool(runtime.get('success')))
print('Deployed     :', deployment.get('success'))
print('Deploy error :', deployment.get('error'))
print('Live URL     :', deployment.get('url'))
print('Health pass  :', health.get('success'))
print('Health checks:', health.get('checks'))
print('Forge score  :', forge.get('score'), '/ 100  grade:', forge.get('grade'))
print('Deploy score :', v11.get('deployment_score'))
