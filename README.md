# alert-analysis

Web application and supporting CLI tools for analyzing OpenShift Dedicated PagerDuty
alert data.

## Requirements
 * Python 3.9+
   * Don't forget to run `pip3 install -r requirements.txt`*
 * a MariaDB/MySQL server*
 * a PagerDuty API token
 * Docker/Podman/K8s/OpenShift (optional)

*If pip complains about being unable to find a specific version of a dependency module, it's probably because you're using a version of Python older than 3.9. If pip instead complains about failing to build the MariaDB module, make sure you have the MariaDB C Connector, GCC, and the Python headers installed (e.g., on Fedora/RHEL: `dnf install mariadb-connector-c mariadb-connector-c-devel gcc python39-devel`).

## Quick Start

 Initial Steps
   Download VSCode that suits your OS.
   Clone this repo onto your VSCode.

 Software Installing Commands

sudo yum install python39 # to install python3.9
python3.9 --version # for checking version of python3.9

sudo yum install mariadb-connector-c
sudo yum install mariadb-connector-c-devel
sudo yum install gcc
sudo yum install python39-devel
pip3.9 install -r requirements.txt

docker # it will prompt you to download docker if it isn’t installed

 Before Initial Caching Database Setup
   Run the below command to pull your database image from docker

    docker pull mariadb

  For creating users in the empty database on the SQL server is as follows,

    docker run --detach --env MARIADB_DATABASE=<database_name> --env MARIADB_USER=<username> --env MARIADB_PASSWORD=<user_password> --env MARIADB_ROOT_HOST=<hostname> --env MARIADB_ROOT_PASSWORD=<host_password> -p 3306:3306 mariadb:latest

  After creating the users i.e the above step, follow the below syntax to write your db_string urls in `.env` file.

    database_username:database_password@database_address:3306/database_name

  After the above steps, you can proceed with populating your database using `updater.py`


## Initial Caching Database Setup
The PagerDuty API is too slow/rate-limited to be used directly by the web application. 
Instead, you'll need to set up, populate, and regularly refresh a caching database.

Create an empty database in your SQL server and two service accounts: one with full
(admin) privileges over the database, and another only with read privileges. Create a
file named `.env` at the root of this repo and fill it in like so:
```bash
AA_PD_API_TOKEN=<your PagerDuty API token>
AA_PD_TEAMS=A_list_of:colon-separated_PagerDuty_team_IDs:that_look_like_this:XY1234Z

# DB_STRINGs should be SQLAlchemy database engine URLs.
# See docs.sqlalchemy.org/en/14/core/engines.html#sqlalchemy.create_engine
AA_RO_DB_STRING=sqlite+pysqlite:///:memory: # Access a read-only account
AA_RW_DB_STRING=sqlite+pysqlite:///:memory: # Access a read-write account

# QUESTION_CLASSES is a list of the questions you'd like to display on the web UI.
# These should be class names from questions.py
AA_QUESTION_CLASSES=QNeverAcknowledged:QNeverAcknowledgedSelfResolved:QFlappingShift
```
*Note*: if you're just experimenting/testing, feel free the leave the SQLite database
string shown above as is. Just know that this will create the database in-memory and
will be dropped as soon as the updater script/web application exists.

Then populate the database using `updater.py`, as specified in the help message shown 
below.
```
$ ./updater.py --help
usage: updater.py [-h] [-s SINCE] [-u UNTIL] [-l LIMIT] [-v]

Updates the OSD alert-analysis tool's cache of PagerDuty incidents and alerts

options:
  -h, --help            show this help message and exit
  -s SINCE, --since SINCE
                        start of the caching time window in ISO-8601 format (default: 30d ago)
  -u UNTIL, --until UNTIL
                        end of the caching time window in ISO-8601 format (default: now)
  -l LIMIT, --limit LIMIT
                        maximum number of incidents to cache (default: 10000)
  -b  DAYS, --backfill  DAYS
                        do a normal run, then check to see if the oldest record in the cache is 
                        at least DAYS days old. if it's not, update cache until it is, batching 
                        in sizes of LIMIT if necessary. --since and --until have no effect after
                        the initial run.
  -v, --verbose
```
Note that this process can take several hours. If successful, output will look like:
```
$ ./updater.py --since="2022-01-01" --until="2022-02-01"
Updating incident cache...done. Cached 9786 incidents.
Updating alert cache...done. Cached 10012 alerts.
```

We recommend setting up a cronjob that runs `./updater.py --backfill 90`
once or twice per day in order to cache at least 90 days of history and refresh the
10,000 most-recently-created incidents. Incidents older than 90 days will not be deleted
from the cache, they're simply not updated.

## Web Application Setup
We recommend running the web application as a container. This project conforms to the
[Source-To-Image](https://github.com/openshift/source-to-image) standard, so you have
several build options.

*Note*: the following instructions assume you've got your database running, populated,
and network-accessible to the execution enviroment (e.g., container) you're about to
create.

### Building and running a Docker image locally
Clone this repo, `cd` into it, and fill out your `.env` file (see above). Then run:
```
docker build . --tag "alert-analysis:latest"
docker run -d -p 8080:8080 --name my_aa --env-file .env alert-analysis:latest
```
Then navigate to http://localhost:8080 in your browser to see the UI. If you get
a blank screen, the application is probably having trouble connecting to your
database. Run `docker logs my_aa` and see the *note* above for more details.

### Deploying on OpenShift
Create and fill out your `.env` file (see above). We'll use `oc new-app` to build our
image. If your OpenShift cluster can access this repository (e.g., because this is a
public repo and your cluster has internet access), run the following to create an app
that OpenShift can automatically rebuild whenever an update is pushed to `main`*:
```bash
export REPO_URL="<Git-clone-able SSH or HTTPS repo URL>"
oc new-app $REPO_URL --strategy=source --env-file=.env --name=my-aa
oc patch svc/my-aa --type=json -p '[{"op": "replace", "path": "/spec/ports/0/port", "value":80}]'
```
If your OpenShift cluster does not have access to this repo (e.g., because this is
private or VPN-restricted repo), clone the repo onto your local machine and run the
following to generate a one-time binary build:
```bash
export SRC_PATH="<full path to a local clone of this repo>"
oc new-app --binary --strategy=docker --env-file=.env --name=my-aa
oc start-build my-aa --from-dir=$SRC_PATH
oc expose deployment my-aa --port 80 --target-port 8080
```
*Note*: the use of the `--env-file` flag bakes your `.env` directly into the generated
`Deployment` in plaintext. A much more secure deployment would instead use `Secret`s and
`ConfigMap`s to provide the necessary config values to the running pod.

Finally, expose the app to the world outside the cluster using:
```bash
oc expose svc/my-aa
echo "Now head to http://$(oc get route/my-aa -o jsonpath={.spec.host})"
```

*To find the webhook URL needed for Git-triggered builds, open the OpenShift web 
console, find the `BuildConfiguration` generated by `oc new-app`, scroll to the bottom 
of the page, and copy the webhook URL shown.

## Web Application Usage
The web application is currently a single-page collection of tables answering the
questions specified by the `AA_QUESTION_CLASSES` config value. The columns of each table
support sorting (click the little arrows next to column name) and filtering (enter your
search term or [filter](https://dash.plotly.com/datatable/filtering#filtering-operators) into the cell below the column name)

## Errata
* Some alert names will be abbreviated when loaded into the cache. See the 
`Alert.standardize_name()` function in `models.py` to see how this works.
