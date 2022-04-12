"""
Classes for displaying PagerDuty analysis results
"""
from dash.dash_table import DataTable
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import questions
from config import QUESTION_CLASSES, RO_DB_STRING
from models import Base


class StandardDataTable(DataTable):
    """
    An opinionated Dash DataTable
    """

    def __init__(self, columns, data):
        """
        Simplified, opinionated constructor for DataTable
        """
        super().__init__(
            row_selectable="single",
            filter_action="native",
            sort_action="native",
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

    def __init__(self, since, until) -> None:
        """
        Constructor for WebUISession. Manages database connection and instantiates
        Question objects

        :param since: a datetime.datetime containing the start of the time window over
            which queries will be evaluated
        :param until: a datetime.datetime containing the end of the time window over
            which queries will be evaluated
        """
        # pylint: disable=invalid-name
        self._since = since
        self._until = until
        self._engine = create_engine(RO_DB_STRING, future=True)
        Session = sessionmaker(bind=self._engine)
        self.db_session = Session()
        Base.metadata.create_all(self._engine)

        self.question_instances = []
        for question_class_name in QUESTION_CLASSES:
            question = getattr(questions, question_class_name)
            self.question_instances.append(question(self.db_session, since, until))
