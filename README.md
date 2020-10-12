# chaoscat

chaoscat is a loadtesting program used to load test Splunk using past Splunk searches.

## Installation

Clone the chaoscat repo and install requirements

```bash
$ git clone https://github.com/beachfires/chaoscat.git 
$ pip3 install -r requirements.txt
```
## Configuration

```ini
[concurrency]
pool_connections = 1000
pool_maxsize = 1000
max_workers = 1000

[chaoscat]
cycle_limit = 1 ; Number of iterations to run
half_load = 1 ; Fires every other search, essentially half the load

[splunk]
search_endpoint = /services/search/jobs

[azure]
username = <azure_username>
password = <azure_username>
base_url = https://splunk.your-org.com:8089

[aws]
username = <aws_username>
password = <aws_password>
base_url = https://splunk-dev.your-org.net:8089
result_count = 0 ; Amount of results for initial Splunk audit search. 0 will return everything. 
search_data = <search string> ; to return Splunk searches to be run for loadtest. This must return a timestamp and a search string
```
## Usage

```bash
$ python3 chaoscat.py
```

