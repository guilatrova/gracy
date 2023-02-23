from typing import Final

TABLE_NAME: Final = "gracy_recordings"

CREATE_RECORDINGS_TABLE: Final = f"""
CREATE TABLE {TABLE_NAME}(
    url VARCHAR(255) NOT NULL,
    method VARCHAR(20) NOT NULL,
    request_body BLOB NULL,
    response BLOB NOT NULL,
    updated_at DATETIME NOT NULL
)
"""

INDEX_RECORDINGS_TABLE: Final = f"""
CREATE UNIQUE INDEX idx_gracy_request
ON {TABLE_NAME}(url, method, request_body)
"""

INDEX_RECORDINGS_TABLE_WITHOUT_REQUEST_BODY: Final = f"""
CREATE UNIQUE INDEX idx_gracy_request_empty_req_body
ON {TABLE_NAME}(url, method)
WHERE request_body IS NULL
"""

INSERT_RECORDING_BASE: Final = f"""
INSERT OR REPLACE INTO {TABLE_NAME}
VALUES (?, ?, ?, ?, ?)
"""

FIND_REQUEST_WITH_REQ_BODY: Final = f"""
SELECT response, updated_at FROM {TABLE_NAME}
WHERE
url = ? AND
method = ? AND
request_body = ?
"""

FIND_REQUEST_WITHOUT_REQ_BODY: Final = f"""
SELECT response, updated_at FROM {TABLE_NAME}
WHERE
url = ? AND
method = ? AND
request_body IS NULL
"""
