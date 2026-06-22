"""Render deployment provider — stub, not yet implemented."""
from app.deployments.base_provider import BaseDeploymentProvider, DeploymentResult

_NOT_IMPL = "Render provider not yet implemented — use provider='railway'"


class RenderProvider(BaseDeploymentProvider):
    def deploy(self, project_path, project_name, env_vars=None) -> DeploymentResult:
        return DeploymentResult(
            success=False, url=None, logs="", error=_NOT_IMPL,
            deploy_id=None, provider="render",
        )

    def get_logs(self, deploy_id: str) -> str:
        return _NOT_IMPL

    def get_status(self, deploy_id: str) -> str:
        return "not_implemented"

    def delete(self, deploy_id: str) -> bool:
        return False
