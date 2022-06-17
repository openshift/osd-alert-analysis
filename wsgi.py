"""
Main WSGI application module
"""
import re
from datetime import date, datetime, timedelta, timezone

import dash_bootstrap_components as dbc
from dash import Dash, dcc, html
from dash.dependencies import Input, Output
from dash.exceptions import PreventUpdate

from webui import StandardDataTable, WebUISession, Region

app = Dash(__name__, external_stylesheets=[dbc.themes.BOOTSTRAP])
application = app.server
app.title = "OSD Alert Analysis"


def get_navbar(since, until, max_date, region):
    """
    Returns navbar page component

    :param since: datetime.datetime for start of query time window
    :param until: datetime.datetime for end of query time window
    :param max_date: maximum datetime.date allowed to be picked by date picker
    :param region: current query Region enum
    """
    return dbc.NavbarSimple(
        [
            dbc.NavItem(
                dbc.InputGroup(
                    [
                        # dbc.InputGroupText("")
                        dcc.DatePickerRange(
                            id="date-picker",
                            start_date=since.date(),
                            end_date=until.date(),
                            max_date_allowed=max_date,
                            updatemode="bothdates",
                            className="dash-bootstrap",
                        ),
                        dbc.Select(
                            id="region-select",
                            options=[
                                {"label": reg.value, "value": reg.value}
                                for _, reg in Region.__members__.items()
                            ],
                            value=region.value,
                        ),
                    ]
                )
            ),
        ],
        brand="OSD Alert Analysis",
        brand_href="#",
    )


def question_answer_generator(question_instances):
    """
    Generates a question header and an answer table for a list of Question instances
    """
    for question in question_instances:
        answer = question.get_answer()
        yield dbc.Row(dbc.Col(html.Br()))
        yield dbc.Row(dbc.Col(html.H2(question.description)))
        yield dbc.Row(
            dbc.Col(
                html.Div(
                    children=[StandardDataTable(answer.columns, answer.data)],
                    className="dbc-row-selectable",
                )
            )
        )


app.layout = html.Div(
    children=[
        dcc.Location(id="url", refresh=False),
        html.Div(id="page-content-div"),
    ]
)


@app.callback(Output("page-content-div", "children"), [Input("url", "pathname")])
def display_page(pathname):
    """
    Generates page content on URL change (i.e., page load)
    """
    max_date = datetime.now(timezone.utc)
    try:
        clean_params_list = re.sub(r"[^\d\w/-]+", "", pathname.strip("/")).rsplit(
            "/", 3
        )
        since = datetime.fromisoformat(clean_params_list[0])
        until = datetime.fromisoformat(clean_params_list[1])
        region = Region(clean_params_list[2])
    except (ValueError, IndexError):
        since = max_date - timedelta(days=7)
        until = max_date
        region = Region.GLOBAL

    session = WebUISession(since, until, region)

    return html.Div(
        [
            get_navbar(since, until, max_date, region),
            dbc.Container(
                list(question_answer_generator(session.question_instances)),
                fluid=False,
            ),
        ]
    )


@app.callback(
    Output("url", "pathname"),
    Input("date-picker", "start_date"),
    Input("date-picker", "end_date"),
    Input("region-select", "value"),
)
def update_date_range(start_date, end_date, region):
    """
    Query date range picker input handler
    """
    path_string = "/"
    try:
        path_string += date.fromisoformat(start_date).isoformat()
        path_string += "/" + date.fromisoformat(end_date).isoformat()
        path_string += "/" + region
    except ValueError:
        # pylint: disable=raise-missing-from
        raise PreventUpdate
    else:
        return path_string


if __name__ == "__main__":
    app.run_server(debug=False, dev_tools_hot_reload=False)
