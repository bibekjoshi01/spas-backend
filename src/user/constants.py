from src.base.constants import BaseEnum


class Genders(BaseEnum):
    MALE = "MALE"
    FEMALE = "FEMALE"
    OTHER = "OTHER"
    RATHER_NOT_TO_SAY = "RATHER_NOT_TO_SAY"


SYSTEM_USER_ROLE = "SYSTEM-USER"
STUDENT_ROLE = "STUDENT"

# Roles the system attaches itself; never offered in a role picker.
INTERNAL_ROLES = (SYSTEM_USER_ROLE, STUDENT_ROLE)


class VerificationTypes(BaseEnum):
    OTP = "OTP"
    LINK = "LINK"
