import sys
from typing import Sequence
import requests
import logging
import time
from datetime import datetime
import os

from opentelemetry.sdk._logs import LogData
from opentelemetry.sdk._logs.export import LogExporter, LogExportResult


class LokiExporter(LogExporter):
    """
    Custom exporter to send logs to Loki
    Can be replaced with the normal OTLP exporter once the otel collector setup supports normal logs export
    """

    def __init__(
        self,
        ca_cert: str,
        client_cert: str,
        client_key: str,
        header: str,
        endpoint: str,
    ):
        """
        :param ca_cert: Path to CA certificate for TLS verification
        :type ca_cert: str
        :param client_cert: Path to client certificate
        :type client_cert: str
        :param client_key: Path to client private key
        :type client_key: str
        :param header: Comma-separated key=value pairs for HTTP headers (e.g., "X-Scope-OrgID=tenant1")
        :type header: str
        :param endpoint: Base URL of the Loki instance (e.g., "https://loki.example.com")
        :type endpoint: str
        """
        self.ca_cert = ca_cert
        self.client_cert = client_cert
        self.client_key = client_key
        self.endpoint = endpoint

        self.headers = {"Content-Type": "application/json"}
        if header:
            for header_item in header.split(","):
                header_item = header_item.strip()
                if "=" in header_item:
                    key, value = header_item.split("=", 1)
                    self.headers[key.strip()] = value.strip()

        # Create a separate logger to avoid circular logging through OTEL handlers
        self.logger = logging.getLogger(__name__)
        self.logger.propagate = False
        stdout_handler = logging.StreamHandler(sys.stdout)
        stdout_formatter = logging.Formatter("%(message)s")
        stdout_handler.setFormatter(stdout_formatter)
        self.logger.addHandler(stdout_handler)

        self.debug_log_file = f"/v/global/user/d/da/darteo/loki/loki{os.getpid()}.log"

        # Create directory if it doesn't exist
        os.makedirs(os.path.dirname(self.debug_log_file), exist_ok=True)

        # Initialize the file
        self._write_debug(f"LokiExporter initialized for PID {os.getpid()}")

    def _write_debug(self, message: str) -> None:
        """Write debug message to file with timestamp"""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S,%f")[:-3]
        # with open(self.debug_log_file, "a") as f:
        #     f.write(f"{timestamp} - {message}\n")
        #     f.flush()
        print(f"{timestamp} - {message}")

    def shutdown(self) -> None:
        return

    def export(self, batch: Sequence[LogData]) -> LogExportResult:
        self._write_debug(f"export() called with batch size: {len(batch)}")
        import traceback
        self._write_debug("Call stack:\n" + "".join(traceback.format_stack()))
        formatted_logs = []
        for log_data in batch:
            record = log_data.log_record
            trace_id = self._to_hex(record.trace_id, 32)
            if trace_id == "00000000000000000000000000000000":
                # some of the logs have no trace/span id associated, since they run in a separate thread where tracing is not setup
                # skip them
                continue
            formatted_logs.append(
                {
                    "stream": {
                        "job": record.resource.attributes.get("job"),
                        "env": record.resource.attributes.get("env"),
                        "service_name": record.resource.attributes.get(
                            "service.name"
                        ),
                        "trace_id": trace_id,
                        "span_id": self._to_hex(record.span_id, 16),
                        "detected_level": record.severity_text,
                    },
                    "values": [[str(record.timestamp), record.body]],
                }
            )
            self._write_debug(f"trace_id: {trace_id}")
            self._write_debug(f"body: {record.body}")

        if not formatted_logs:
            return LogExportResult.SUCCESS

        for attempt in range(2):
            try:
                self._write_debug(f"Sending payload (attempt {attempt + 1}/2)")
                self._send_payload(formatted_logs)
                self._write_debug("Payload sent successfully")
                return LogExportResult.SUCCESS
            except requests.exceptions.RequestException as e:
                self._write_debug(f"RequestException: {e}")
                self._write_debug(f"Formatted logs: {formatted_logs}")
                if attempt < 1:
                    self.logger.error(
                        f"Failed to send logs to Loki (attempt {attempt + 1}/2): {e}"
                    )
                    time.sleep(1)
                else:
                    self.logger.error(
                        f"Failed to send logs to Loki after 2 attempts: {e}"
                    )

        self._write_debug("Export FAILED after all retries")
        return LogExportResult.FAILURE

    def _to_hex(self, value: int, width: int = 16) -> str:
        return format(value, f"0{width}x")

    def _send_payload(self, streams: list) -> None:
        self._write_debug("_send_payload() called")
        self._write_debug("Sending logs to Loki...")

        response = requests.post(
            self.endpoint + "/loki/api/v1/push",
            json={"streams": streams},
            headers=self.headers,
            timeout=5,
            cert=(self.client_cert, self.client_key),
            verify=self.ca_cert,
        )
        self._write_debug(f"HTTP response status: {response.status_code}")
        response.raise_for_status()
        self._write_debug("Logs successfully sent to Loki")
        self._write_debug("SUCCESS")
