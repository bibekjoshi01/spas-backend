from src.base.constants import BaseEnum


class StudentStatus(BaseEnum):
    """Overall standing of a student in the college."""

    STUDYING = "STUDYING"
    GRADUATED = "GRADUATED"
    DROPPED_OUT = "DROPPED_OUT"
    TRANSFERRED = "TRANSFERRED"


class SemesterEnrollmentStatus(BaseEnum):
    """Standing of a student within one semester."""

    ACTIVE = "ACTIVE"
    COMPLETED = "COMPLETED"
    WITHDRAWN = "WITHDRAWN"
