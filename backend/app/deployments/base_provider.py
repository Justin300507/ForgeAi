from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class DeploymentResult:
    success: bool
    url: str | None
    logs: str
    error: str | None
    deploy_id: str | None
    provider: str
    metadata: dict = field(default_factory=dict)


class BaseDeploymentProvider(ABC):
    @abstractmethod
    def deploy(
        self,
        project_path: str,
        project_name: str,
        env_vars: dict | None = None,
    ) -> DeploymentResult: ...

    @abstractmethod
    def get_logs(self, deploy_id: str) -> str: ...

    @abstractmethod
    def get_status(self, deploy_id: str) -> str: ...

    @abstractmethod
    def delete(self, deploy_id: str) -> bool: ...
