from gracy.models import DEFAULT_CONFIG, GracyConfig
from gracy.sync.requester import Gracy, graceful


class TestAPI(Gracy[str]):
    @graceful(DEFAULT_CONFIG)
    def get_sample(self):
        print("get_sample executed")
        return self._get("a")

    def do_that(self):
        pass
