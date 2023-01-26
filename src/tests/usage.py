import logging
from http import HTTPStatus

from gracy.decorators import frame
from gracy.models import BaseEndpoint
from gracy.sync.requester import Gracy

logger = logging.getLogger(__name__)

# TODO: Needs to add pagination.


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


class SnykAPIClient(Gracy[SnykEndpoint]):
    BASE_URL = "https://api.snyk.io"
    MAX_RETRIES = 3
    WAIT_SECONDS_GENERIC_ERROR = 3

    # def __init__(self, config: SnykConfig) -> None:
    #     self._config = config
    #     self._skip_issue_paths = SnykSettings().SKIP_ISSUE_PATHS
    #     self._session = Session(
    #         base_url=self.BASE_URL,
    #         headers={"Authorization": f"token {self._config.api_key}"},
    #         retry_options=ExponentialRetry(
    #             attempts=self.MAX_RETRIES,
    #             start_timeout=self.WAIT_SECONDS_GENERIC_ERROR,
    #             statuses=set(
    #                 DEFAULT_STATUS_RETRY_LIST
    #                 + [
    #                     HTTPStatus.UNAUTHORIZED.value,
    #                     HTTPStatus.FORBIDDEN.value,
    #                     HTTPStatus.NOT_FOUND.value,
    #                 ]
    #             ),
    #         ),
    #     )

    async def _get(self, endpoint: str, headers: dict | None = None, retry: bool = True) -> dict:
        async with self._session.get(str(endpoint), retry_if_configured=retry, headers=headers) as resp:
            resp_json = await resp.json()
            return resp_json

    async def _post(self, endpoint: str, json: dict | None = None, retry: bool = True) -> dict:
        async with self._session.post(endpoint, json=json, retry_if_configured=retry) as resp:
            resp_json = await resp.json()
            return resp_json

    async def get_organizations(self) -> list[dict]:
        res = await self._get(SnykEndpoint.LIST_ORGS)
        return res["orgs"]

    async def list_all_projects(self, org_id: str) -> list[dict]:
        # https://snyk.docs.apiary.io/#reference/projects/all-projects/list-all-projects?console=1
        return self.get(
            SnykEndpoint.LIST_ALL_PROJECTS,
            {"ORG_ID": org_id},
            strict_status_code=200,
            log_before=False,
            log_errors="CRITICAL",
            throttle=[(200, 10), (404, 20)],
        )

    @frame()
    async def list_all_projects2(self, org_id: str) -> list[dict]:
        # https://snyk.docs.apiary.io/#reference/projects/all-projects/list-all-projects?console=1
        return self.get(SnykEndpoint.LIST_ALL_PROJECTS, {"ORG_ID": org_id})

    async def list_aggregated_issues(
        self, org_id: str, project_id: str, include_description: bool = True, include_introduce_through: bool = False
    ) -> list[dict]:
        # https://snyk.docs.apiary.io/#reference/projects/aggregated-project-issues/list-all-aggregated-issues
        res = await self._post(
            SnykEndpoint.LIST_AGGREGATED_ISSUES.format(ORG_ID=org_id, PROJ_ID=project_id),
            json={"includeDescription": include_description, "includeIntroducedThrough": include_introduce_through},
        )
        return res["issues"]

    async def get_issue_path(self, org_id: str, project_id: str, issue_id: str) -> dict | None:
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
        if self._skip_issue_paths:
            return None

        url = SnykEndpoint.LIST_ISSUE_PATHS.format(ORG_ID=org_id, PROJ_ID=project_id, ISSUE_ID=issue_id)
        try:
            resp = await self._get(url, retry=False)
        except ClientResponseError as ex:
            # NOTE: This is just metadata related to dependency chain, so it has less impact.
            # It's not essential for someone to fix the finding.
            # We've some captured data on intermitted failures on this specific endpoint
            # Sometimes Snyk responds with:
            # 404: { "error": "Issue not found in selected snapshot" }
            # other times with:
            # 403, 5xx like 500, 502, 503 without a clear reason...
            logger.warning(
                f"Snyk replied {ex.status} for {url}. "
                "Skipping this error as this dependency chain metadata call is not critical to a sync",
                extra={"status_code": ex.status, "url": url},
            )
            return None

        else:
            return resp

    async def list_aggregated_sast_issues(self, org_id: str, project_id: str) -> list[dict]:
        """
        Special endpoint that uses the experimental api to list SAST issues. It doesn't exist yet on the new endpoints
        """
        # https://apidocs.snyk.io/?version=2022-04-06~experimental#tag--Issues
        headers = {"Content-Type": "application/vnd.api+json", "Authorization": f"token {self._base_config.api_key}"}
        url = SnykEndpoint.LIST_AGGREGATED_SAST_ISSUES.format(ORG_ID=org_id, PROJ_ID=project_id)
        done = False
        data = []
        while not done:
            res = await self._get(url, headers=headers)
            data += res["data"]
            if "next" in res["links"]:
                url = f'/rest/{res["links"]["next"]}'
            else:
                done = True
                return data
        return []

    async def get_sast_issue_details(self, org_id: str, issue_id: str, project_id: str) -> dict:
        """
        Special endpoint that uses the experimental api to list SAST issues. It doesn't exist yet on the new endpoints
        """
        # https://apidocs.snyk.io/?version=2022-04-06~experimental#tag--Issues
        headers = {"Content-Type": "application/vnd.api+json", "Authorization": f"token {self._base_config.api_key}"}
        url = SnykEndpoint.GET_SAST_ISSUE_DETAIL.format(ORG_ID=org_id, ISSUE_ID=issue_id, PROJ_ID=project_id)
        res = await self._get(url, headers=headers)
        return res["data"]
