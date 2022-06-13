"""
Classes for displaying PagerDuty analysis results
"""
from enum import Enum
from dash.dash_table import DataTable
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import questions
from config import QUESTION_CLASSES, RO_DB_STRING
from models import Base


class Region(Enum):
    """
    Enumerated type for on-call regions. Should roughly match regions defined in DB's
    shift column
    """

    GLOBAL = "Global"
    APAC = "APAC"
    EMEA = "EMEA"
    NASA = "NASA"


class StandardDataTable(DataTable):
    """
    An opinionated Dash DataTable
    """

    def __init__(self, columns, data):
        """
        Simplified, opinionated constructor for DataTable
        """
        # Sort by the last column in descending order
        sorted_col = list(cd["id"] for cd in columns)[-1]
        super().__init__(
            row_selectable="single",
            filter_action="native",
            sort_action="native",
            sort_by=[{"column_id": sorted_col, "direction": "desc"}],
            sort_mode="multi",
            page_action="native",
            page_current=0,
            page_size=10,
            data=data,
            columns=columns,
            style_cell={"font-family": "sans-serif"},
        )


class WebUISession:
    """
    Singleton connection holder
    """

    def __init__(self, since, until, region) -> None:
        """
        Constructor for WebUISession. Manages database connection and instantiates
        Question objects

        :param since: a datetime.datetime containing the start of the time window over
            which queries will be evaluated
        :param until: a datetime.datetime containing the end of the time window over
            which queries will be evaluated
        :param region: a Region enum representing the region of interest
        """
        # pylint: disable=invalid-name
        self._since = since
        self._until = until
        self._region = region
        self._engine = create_engine(RO_DB_STRING, future=True)
        Session = sessionmaker(bind=self._engine)
        self.db_session = Session()
        Base.metadata.create_all(self._engine)

        self.question_instances = []
        for question_class_name in QUESTION_CLASSES:
            question = getattr(questions, question_class_name)
            self.question_instances.append(
                question(self.db_session, since, until, region.value)
            )
