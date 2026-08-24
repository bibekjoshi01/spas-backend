from src.base.constants import BaseEnum


class AttendanceStatus(BaseEnum):
    PRESENT = "PRESENT"
    ABSENT = "ABSENT"
    LATE = "LATE"
    EXCUSED = "EXCUSED"


class InternalExamType(BaseEnum):
    """Kind of internal assessment, so results compare across subjects."""

    UNIT_TEST = "UNIT_TEST"
    FIRST_TERM = "FIRST_TERM"
    SECOND_TERM = "SECOND_TERM"
    PRE_BOARD = "PRE_BOARD"
    OTHER = "OTHER"


class AssignmentStatus(BaseEnum):
    DONE = "DONE"
    PARTIAL = "PARTIAL"
    NOT_DONE = "NOT_DONE"
