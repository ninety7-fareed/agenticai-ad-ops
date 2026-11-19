"""Windows service for the always-available ADPilot chat. Used on LAB02."""

import threading

try:
    import servicemanager
    import win32event
    import win32service
    import win32serviceutil
except ImportError:
    servicemanager = None
    win32event = None
    win32service = None
    win32serviceutil = None


if win32serviceutil is not None:
    class AdpilotService(win32serviceutil.ServiceFramework):
        _svc_name_ = "ADPilot"
        _svc_display_name_ = "ADPilot"
        _svc_description_ = "Always-available ADPilot chat for the test OU."

        def __init__(self, args):
            win32serviceutil.ServiceFramework.__init__(self, args)
            self.stop_event = win32event.CreateEvent(None, 0, 0, None)
            self.server = None
            self.slack_handler = None
            self.slack_thread = None

        def SvcStop(self):
            self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
            if self.server is not None:
                self.server.shutdown()
            if self.slack_handler is not None:
                self.slack_handler.close()
            win32event.SetEvent(self.stop_event)

        def SvcDoRun(self):
            from server import open_server

            servicemanager.LogInfoMsg("ADPilot service is starting")
            self.server = open_server()
            worker = threading.Thread(target=self.server.serve_forever)
            worker.daemon = True
            worker.start()
            try:
                from slack_integration import create_slack_handler

                self.slack_handler = create_slack_handler()
                if self.slack_handler is not None:
                    self.slack_thread = threading.Thread(
                        target=self._run_slack, name="ADPilotSlack", daemon=True,
                    )
                    self.slack_thread.start()
                else:
                    servicemanager.LogInfoMsg("ADPilot Slack integration is not configured")
            except Exception as error:
                message = "ADPilot Slack integration failed to initialize: {0}".format(error)
                servicemanager.LogErrorMsg(message)
                from server import log

                log(message)
            win32event.WaitForSingleObject(self.stop_event, win32event.INFINITE)
            worker.join(timeout=10)
            if self.slack_thread is not None:
                self.slack_thread.join(timeout=10)

        def _run_slack(self):
            from server import log

            while True:
                try:
                    self.slack_handler.connect()
                    if self.slack_handler.client.is_connected():
                        log("ADPilot Slack Socket Mode connected")
                    else:
                        log(
                            "ADPilot Slack Socket Mode connect attempt ended without an active connection; "
                            "automatic reconnect remains enabled"
                        )
                    return
                except Exception as error:
                    error_type = "{0}.{1}".format(
                        type(error).__module__, type(error).__name__,
                    )
                    message = "ADPilot Slack connection attempt failed: {0}".format(error_type)
                    servicemanager.LogErrorMsg(message)
                    log(message)
                    wait_result = win32event.WaitForSingleObject(self.stop_event, 30000)
                    if wait_result != win32event.WAIT_TIMEOUT:
                        return


if __name__ == "__main__":
    if win32serviceutil is None:
        raise SystemExit("pywin32 is required to install the ADPilot Windows service")
    win32serviceutil.HandleCommandLine(AdpilotService)
