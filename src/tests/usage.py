import logging
from http import HTTPStatus
from typing import Any

from gracy.core import Gracy, graceful
from gracy.exceptions import BadResponse
from gracy.models import BaseEndpoint, GracefulRetry, GracyConfig, LogEvent, LogLevel

logger = logging.getLogger(__name__)


class SnykEndpoint(BaseEndpoint):
    LIST_ORGS = "/api/v1/orgs"
    LIST_ALL_PROJECTS = "/api/v1/org/{ORG_ID}/projects"
    LIST_AGGREGATED_ISSUES = "/api/v1/org/{ORG_ID}/project/{PROJ_ID}/aggregated-issues"
    LIST_ISSUE_PATHS = "/api/v1/org/{ORG_ID}/project/{PROJ_ID}/issue/{ISSUE_ID}/paths"

    # New API, only available at this old date :/
    LIST_AGGREGATED_SAST_ISSUES = "/rest/orgs/{ORG_ID}/issues?project_id={PROJ_ID}&version=2022-04-06~experimental"
    GET_SAST_ISSUE_DETAIL = (
        "/rest/orgs/{ORG_ID}/issues/detail/code/{ISSUE_ID}?project_id={PROJ_ID}&version=2022-04-06~experimental"
    )


RETRY_STATUS = {
    HTTPStatus.TOO_MANY_REQUESTS,
    HTTPStatus.LOCKED,
    HTTPStatus.INTERNAL_SERVER_ERROR,
    HTTPStatus.BAD_GATEWAY,
    HTTPStatus.SERVICE_UNAVAILABLE,
    HTTPStatus.GATEWAY_TIMEOUT,
    HTTPStatus.UNAUTHORIZED,
    HTTPStatus.FORBIDDEN,
    HTTPStatus.NOT_FOUND,
}


class SnykAPIClient(Gracy[SnykEndpoint]):
    class Config:  # type: ignore
        BASE_URL = "https://api.snyk.io"
        SETTINGS = GracyConfig(
            retry=GracefulRetry(
                delay=3,
                max_attempts=3,
                retry_on=RETRY_STATUS,
                log_before=LogEvent(LogLevel.INFO),
                log_after=LogEvent(LogLevel.INFO),
                log_exhausted=LogEvent(LogLevel.CRITICAL),
            ),
        )

    def __init__(self) -> None:
        super().__init__()

    async def _get_json(
        self,
        endpoint: SnykEndpoint | str,
        format: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        res = await self._get(endpoint, format, headers=headers)
        return res.json()

    async def _post_json(
        self,
        endpoint: SnykEndpoint | str,
        format: dict[str, str] | None = None,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        res = await self._post(endpoint, format, json=json)
        return res.json()

    async def get_organizations(self) -> list[dict[str, Any]]:
        res = await self._get_json(SnykEndpoint.LIST_ORGS)
        return res["orgs"]

    async def list_all_projects(self, org_id: str) -> list[dict[str, Any]]:
        """https://snyk.docs.apiary.io/#reference/projects/all-projects/list-all-projects?console=1"""
        res = await self._post_json(SnykEndpoint.LIST_ALL_PROJECTS, dict(ORG_ID=org_id))
        return res["projects"]

    async def list_aggregated_issues(
        self, org_id: str, project_id: str, include_description: bool = True, include_introduce_through: bool = False
    ) -> list[dict[str, Any]]:
        """https://snyk.docs.apiary.io/#reference/projects/aggregated-project-issues/list-all-aggregated-issues"""
        res = await self._post_json(
            SnykEndpoint.LIST_AGGREGATED_ISSUES,
            dict(ORG_ID=org_id, PROJ_ID=project_id),
            json={"includeDescription": include_description, "includeIntroducedThrough": include_introduce_through},
        )
        return res["issues"]

    @graceful(
        retry=None,
        log_errors=LogEvent(
            LogLevel.WARNING,
            custom_message=(
                "Snyk replied {STATUS} for {URL}. "
                "Skipping this error as this dependency chain metadata call is not critical to a sync"
            ),
        ),
    )
    async def get_issue_path(self, org_id: str, project_id: str, issue_id: str) -> dict[str, Any] | None:
        """
        https://docs.snyk.io/snyk-api-info/using-snyk-api/snyk-api-v1-path-endpoint-information
        https://snyk.docs.apiary.io/#reference/projects/activate-an-individual-project/list-all-project-issue-paths

        Paths contain information about version issues/fixes
        e.g.

        ```json
        "paths": [
            [
            {
                "name": "pg-promise",
                "version": "4.8.1",
                "fixVersion": "5.9.2"
            },
            {
                "name": "pg",
                "version": "5.1.0"
            }
            ]
        ]
        ```
        """
        # if self._skip_issue_paths:
        #     return None

        try:
            resp = await self._get_json(
                SnykEndpoint.LIST_ISSUE_PATHS,
                dict(ORG_ID=org_id, PROJ_ID=project_id, ISSUE_ID=issue_id),
            )

        except BadResponse:
            return None

        else:
            return resp

    async def list_aggregated_sast_issues(self, org_id: str, project_id: str) -> list[dict[str, Any]]:
        """
        Special endpoint that uses the experimental api to list SAST issues. It doesn't exist yet on the new endpoints
        https://apidocs.snyk.io/?version=2022-04-06~experimental#tag--Issues
        """
        headers = {"Content-Type": "application/vnd.api+json", "Authorization": f"token {self._base_config.api_key}"}
        done = False
        data = []

        url = SnykEndpoint.LIST_AGGREGATED_SAST_ISSUES.format(ORG_ID=org_id, PROJ_ID=project_id)

        while not done:
            res = await self._get_json(
                url,
                None,
                headers=headers,
            )

            data += res["data"]

            if "next" in res["links"]:
                url = f'/rest/{res["links"]["next"]}'
            else:
                done = True
                return data

        return []

    async def get_sast_issue_details(self, org_id: str, issue_id: str, project_id: str) -> dict[str, Any]:
        """
        Special endpoint that uses the experimental api to list SAST issues. It doesn't exist yet on the new endpoints
        """
        # https://apidocs.snyk.io/?version=2022-04-06~experimental#tag--Issues
        headers = {"Content-Type": "application/vnd.api+json", "Authorization": f"token {self._base_config.api_key}"}

        res = await self._get_json(
            SnykEndpoint.GET_SAST_ISSUE_DETAIL,
            dict(ORG_ID=org_id, ISSUE_ID=issue_id, PROJ_ID=project_id),
            headers=headers,
        )
        return res["data"]
