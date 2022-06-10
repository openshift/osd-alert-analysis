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


def get_navbar(since, until, max_date):
    """
    Returns navbar page component

    :param since: datetime.datetime for start of query time window
    :param until: datetime.datetime for end of query time window
    :max_date: maximum datetime.date allowed to be picked by date picker
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
                        dbc.DropdownMenu(
                            [
                                dbc.DropdownMenuItem("Global", id="global-reg-btn"),
                                dbc.DropdownMenuItem("APAC", id="apac-reg-btn"),
                                dbc.DropdownMenuItem("EMEA", id="emea-reg-btn"),
                                dbc.DropdownMenuItem("NASA", id="nasa-reg-btn"),
                            ],
                            label="Region",
                            align_end=True,
                            addon_type="append",
                            color="secondary",
                            in_navbar=True,
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
    max_date = datetime.now(timezone.utc) - timedelta(days=1)
    try:
        clean_dates_list = re.sub(r"[^\d/-]+", "", pathname.strip("/")).rsplit("/", 2)
        since = datetime.fromisoformat(clean_dates_list[0])
        until = datetime.fromisoformat(clean_dates_list[1])
    except (ValueError, IndexError):
        since = max_date - timedelta(days=30)
        until = max_date

    session = WebUISession(since, until)

    return html.Div(
        [
            get_navbar(since, until, max_date),
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
    Input("global-reg-btn", "n_clicks_timestamp"),
    Input("apac-reg-btn", "n_clicks_timestamp"),
    Input("emea-reg-btn", "n_clicks_timestamp"),
    Input("nasa-reg-btn", "n_clicks_timestamp"),
)
def update_date_range(start_date, end_date, global_ts, apac_ts, emea_ts, nasa_ts):
    """
    Query date range picker input handler
    """
    path_string = "/"
    try:
        path_string += date.fromisoformat(start_date).isoformat()
        path_string += "/" + date.fromisoformat(end_date).isoformat()
        path_string
    except ValueError:
        # pylint: disable=raise-missing-from
        raise PreventUpdate
    else:
        return path_string


def determine_selected_region(global_ts, apac_ts, emea_ts, nasa_ts):
    """
    Determines the region that was selected via dropdown menu
    """
    clicked_btn = max(global_ts, apac_ts, emea_ts, nasa_ts)

    if clicked_btn == apac_ts:
        return Region.APAC
    elif clicked_btn == emea_ts:
        return Region.EMEA
    elif clicked_btn == nasa_ts:
        return Region.NASA
    else:
        return Region.GLOBAL


if __name__ == "__main__":
    app.run_server(debug=False, dev_tools_hot_reload=False)
