from ride.sdk import PythonControllerAdapter

CONTROLLER_TYPE = "course-starter"


class Adapter(PythonControllerAdapter):
    module = "course_ride_controller"
    role = CONTROLLER_TYPE
