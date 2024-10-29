import time
from dataclasses import dataclass
from enum import Enum
from typing import List, Optional

import serial

from modem.exceptions import ATConnectionError, SerialSafeReadFailed, SerialSafeWriteFailed


class ATCommand(Enum):
    AT = "AT"

    SET_ECHO_MODE = "ATE"
    SET_CMD_LINE_TERM = "ATS3"
    SET_RESP_FORMAT_CHAR = "ATS4"
    SET_CMD_LINE_END_CHAR = "ATS5"

    RESET_TO_FACTORY = "AT&F"

    QUERY_CONFIGURATION = "AT+QCFG"
    QUERY_ENGINEERING_MODE = "AT+QENG"
    QUERY_PING= "AT+QPING"
    CONFIGURE_FUNCTIONALITY = "AT+CFUN"
    CONFIGURE_PDP_CONTEXT = "AT+CGDCONT"
    CHECK_SIGNAL_QUALITY = "AT+CSQ"
    CONFIGURE_OPERATOR = "AT+COPS"
    CONFIGURE_CLOCK = "AT+CCLK"


class ATDivider(Enum):
    UNDEFINED = ""
    EQ = "="
    QUESTION = "?"


class ATResultCode(Enum):
    OK = "OK"
    CONNECT = "CONNECT"
    RING = "RING"
    NO_CARRIER = "NO CARRIER"
    ERROR = "ERROR"
    NO_DIALTONE = "NO DIALTONE"
    BUSY = "BUSY"
    NO_ANSWER = "NO ANSWER"


@dataclass
class ATResponse:
    status: ATResultCode

    # List of responses split by , for each line split by \r\n
    data: Optional[List[List[str]]] = None


class ATCommander:
    def __init__(self, port: str, baud: int = 115200):
        self.port = port
        self.baud = baud
        self.ser = serial.Serial(self.port, self.baud)
        self.ser.timeout = 5 # Max timeout

        # Clear buffers
        self.ser.flush()
        self.ser.read_all()

        if not self.check_ok():
            raise ATConnectionError("Failed to read 'OK' from serial port")

        # TODO - This is very slow, we must cache it after some time
        self._configure_terminators()

    def _configure_terminators(self) -> None:
        # Set terminators
        self.command(ATCommand.SET_CMD_LINE_TERM, ATDivider.EQ, "13")
        self.command(ATCommand.SET_RESP_FORMAT_CHAR, ATDivider.EQ, "10")

        self.command(ATCommand.SET_CMD_LINE_END_CHAR, ATDivider.EQ, "13")

        # Disable ECHO
        self.command(ATCommand.SET_ECHO_MODE, ATDivider.UNDEFINED, "0")

    def _close(self) -> None:
        if self.ser and self.ser.is_open:
            self.ser.close()

    def _parse_response(self, response: str, cmd_id_response: Optional[str] = None) -> ATResponse:
        parts = [
            part
            for part in (response.split('\r\n') if '\r\n' in response else response.split('\n'))
            if part
        ]

        data = None
        if cmd_id_response:
            data = [
                [ piece if piece != '-' else None for piece in part.split(f'{cmd_id_response}: ')[1].replace('"', '').split(',') ]
                for part in parts
                if cmd_id_response in part
            ]

        status = [
            code
            for code in ATResultCode
            if code.value in response
        ]

        return ATResponse(
            status=status[0] if len(status) > 0 else ATResultCode.ERROR,
            data=data
        )

    def _cmd_read_response(self, cmd_id_response: Optional[str] = None) -> ATResponse:
        buffer: str = ""
        try:
            iter_delay = 0.1
            max_iter = int(self.ser.timeout / iter_delay)
            # We should read till one of ATResultCode be found and if we have a cmd_id_response we should also wait it
            for _ in range(0, max_iter):
                buffer += self.ser.read_all().decode("ascii")

                if ATResultCode.ERROR.value in buffer:
                    raise SerialSafeReadFailed("Error found in response")

                print(buffer)
                if any(code.value in buffer for code in ATResultCode):
                    if cmd_id_response is None or cmd_id_response in buffer:
                        return self._parse_response(buffer, cmd_id_response)
                time.sleep(iter_delay)

            raise SerialSafeReadFailed("Max timeout reached while waiting for response")
        except Exception as e:
            raise SerialSafeReadFailed(f"Failed to read all bytes from serial device at {self.port}") from e

    def _safe_serial_write(self, data: str) -> None:
        bytes_written = self.ser.write(data.encode("ascii"))
        self.ser.flush()
        if bytes_written != len(data):
            raise SerialSafeWriteFailed(f"Failed to write all bytes to serial device at {self.port}")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self._close()

    def raw_command(
        self,
        command: str,
        delay: Optional[int] = 0.3,
        cmd_id_response: Optional[str] = None
    ) -> ATResponse:
        print("Command:", command)
        self._safe_serial_write(f"{command}\r\n")

        # When we don't have a response to wait for, we should wait before reading, average is 300ms
        if cmd_id_response is None:
            time.sleep(delay)

        return self._cmd_read_response(cmd_id_response)

    def command(
        self,
        command: ATCommand,
        divider: ATDivider = ATDivider.UNDEFINED,
        data: str = "",
        cmd_id_response: bool = True
    ) -> ATResponse:
        # If commands have AT+ it should include in response it, for async commands like AT+QPING
        # that will return OK as soon as hit, but after some time return the result as +QPING: ......
        expected_cmd_id = f"+{command.value.split('+')[1]}" if "AT+" in command.value and cmd_id_response else None

        return self.raw_command(f"{command.value}{divider.value}{data}\r\n", cmd_id_response=expected_cmd_id)

    def check_ok(self) -> bool:
        response = self.command(ATCommand.AT)
        # We need to cover cases where terminators are not set
        return response.status == ATResultCode.OK

    def get_signal_strength(self) -> ATResponse:
        return self.command(ATCommand.CHECK_SIGNAL_QUALITY)

    def get_network_info(self) -> ATResponse:
        return self.command(ATCommand.CONFIGURE_OPERATOR, ATDivider.QUESTION)

    def get_pdp_info(self) -> ATResponse:
        return self.command(ATCommand.CONFIGURE_PDP_CONTEXT, ATDivider.QUESTION)

    def get_neighboring_cells(self) -> ATResponse:
        return self.command(ATCommand.QUERY_ENGINEERING_MODE, ATDivider.EQ, '"neighbourcell"')

    def get_serving_cell(self) -> ATResponse:
        return self.command(ATCommand.QUERY_ENGINEERING_MODE, ATDivider.EQ, '"servingcell"')

    def get_clock(self) -> ATResponse:
        return self.command(ATCommand.CONFIGURE_CLOCK, ATDivider.QUESTION)

    def reboot_modem(self) -> ATResponse:
        return self.command(ATCommand.CONFIGURE_FUNCTIONALITY, ATDivider.EQ, '1,1', cmd_id_response=False)

    def disable_modem(self) -> ATResponse:
        return self.command(ATCommand.CONFIGURE_FUNCTIONALITY, ATDivider.EQ, '0,1', cmd_id_response=False)

    def reset_to_factory(self) -> ATResponse:
        return self.command(ATCommand.RESET_TO_FACTORY)

    def ping(self, host: str, n: int = 4) -> ATResponse:
        return self.command(ATCommand.QUERY_PING, ATDivider.EQ, f'1,"{host}",{n},1')
