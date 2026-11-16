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

        def SvcStop(self):
            self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
            if self.server is not None:
                self.server.shutdown()
            win32event.SetEvent(self.stop_event)

        def SvcDoRun(self):
            from server import open_server

            servicemanager.LogInfoMsg("ADPilot service is starting")
            self.server = open_server()
            worker = threading.Thread(target=self.server.serve_forever)
            worker.daemon = True
            worker.start()
            win32event.WaitForSingleObject(self.stop_event, win32event.INFINITE)
            worker.join(timeout=10)


if __name__ == "__main__":
    if win32serviceutil is None:
        raise SystemExit("pywin32 is required to install the ADPilot Windows service")
    win32serviceutil.HandleCommandLine(AdpilotService)
