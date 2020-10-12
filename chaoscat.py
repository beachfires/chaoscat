import os
import time
import random
import logging
import urllib3
import requests
import configparser
import concurrent.futures
from collections import Counter
from xml.etree import ElementTree
from datetime import datetime, timedelta




# Surpress invalid cert warning
#urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Logging Configuration
format = '%(asctime)s: %(levelname)s: %(message)s'
datefmt = '%d-%m-%Y %H:%M:%S'
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)
ch = logging.StreamHandler()
ch.setLevel(logging.DEBUG)
formatter = logging.Formatter(fmt=format, datefmt=datefmt)
ch.setFormatter(formatter)
logger.addHandler(ch)


def fire_search(session, endpoint, start_datetime, search,
                thread_n, verify=False):
    '''Fires Splunk search at the same relative time to start_datetime.
       This is achieved by adding the mins:secs:msecs to start_datetime
       (search_datetime_calc), and then comparing it to UTC now to run
       when it matches. If it's in the future, we sleep the difference.
       Otherwise if it's in the past, it will be run straight away.
       '''
    search_time = search['_time']
    search_query = search['search']
    search_earliest = search.get('searchStartTime', '-24h')
    search_latest = search.get('searchEndTime', 'now')
    clean_search_query = search_query.replace('\n', ' ')[:20]
    datetime_now_utc = datetime.utcnow()
    # Extracting Minutes, Seconds and Milliseconds from _time
    search_datetime = datetime.strptime(search_time[14:-6], '%M:%S.%f')
    # Create delta using the Splunk search _time m:s:ms
    time_to_add = timedelta(microseconds=search_datetime.microsecond,
                            seconds=search_datetime.second,
                            minutes=search_datetime.minute
                            )
    # Add delta to start time to get time search should run
    search_datetime_calc = start_datetime + time_to_add
    logger.debug('Started: thread={}, utc_time_now={}, '
                 'search_time={}, search_earliest={}, '
                 'search_latest={}, search={}'.format(thread_n,
                                                      datetime_now_utc,
                                                      search_datetime_calc,
                                                      search_earliest,
                                                      search_latest,
                                                      clean_search_query))

    # if search_datetime_calc > datetime_now_utc we wait the difference.
    time_delta = timedelta(0)
    if search_datetime_calc > datetime_now_utc:
        time_delta = search_datetime_calc - datetime_now_utc
        logger.debug('Waiting: thread={}, utc_time_now={}, search_time={}, '
                     'wait_secs={}, wait_msecs={} search={}'.format(
                         thread_n, datetime_now_utc, search_datetime_calc,
                         time_delta.seconds, time_delta.microseconds,
                         clean_search_query
                         ))
        time.sleep(time_delta.seconds)

    # Added random timeout (TTL) so that not all the scheduled searches
    # dispatch artefacts need to be reaped at the same time
    # - causing IO bottleneck.
    rand_timeout = random.randint(600, 1400)
    # We slice off the start and end of search_query as they seem to be
    # a single quote which is escaped by requests.post and therefore
    # causing a faulty splunk search where we get a 400 bad request
    data = {'search': search_query[1:-1], 'earliest_time': search_earliest,
            'latest_time': search_latest, 'timeout': rand_timeout
            }
    response = session.post(endpoint, data=data, verify=verify)
    #  logger.debug(response.text) # See azure search response text
    time_taken = datetime.utcnow() - datetime_now_utc
    logger.debug('Completed: thread={}, utc_time_now={}, search_time={}, '
                 'response_code={}, rand_timeout={}, '
                 'time_taken_secs={}, search={}'.format(
                     thread_n, datetime_now_utc, search_datetime_calc,
                     response.status_code, rand_timeout, time_taken.seconds
                     clean_search_query
                     ))
    return response.status_code, time_delta.seconds


def post_splunk_search(session, endpoint, data, verify=False):
    '''Fires Splunk search and returns SID'''
    logger.info('Running Splunk search query...')
    response = session.post(endpoint, data=data, verify=verify)

    if response.status_code == 201:
        logger.info('Splunk search completed successfully.')
        logger.info('Extracting SID...')
        tree = ElementTree.fromstring(response.text)
        sid = tree.find('sid').text
        logger.info('Successfully extracted SID: {sid}'.format(sid=sid))
        return sid
    else:
        raise requests.HTTPError(response)


def poll_splunk_search(session, endpoint, sid, max_attempts=10,
                       poll_wait=5, verify=False):
    '''Polls Splunk using SID, waiting poll_wait seconds between
       polls, blocking execution until max_attempts reached where
       an exception is raised'''
    attempt = 1
    success_str = '<s:key name="isDone">1</s:key>'
    poll_endpoint = '{endpoint}/{sid}/'.format(endpoint=endpoint, sid=sid)

    logger.info('Polling Splunk search using SID... attempt 1')
    response = session.get(poll_endpoint, verify=verify)
    while success_str not in response.text:
        if attempt == max_attempts:
            raise requests.HTTPError(response)  # TODO: need more specific exception
        logger.info('Polling Splunk search using SID... attempt {}'.format(
                                                                attempt + 1))
        response = session.get(poll_endpoint, verify=verify)
        attempt += 1
        logger.info('Waiting {} seconds before polling again...'.format(
                                                               poll_wait))
        time.sleep(poll_wait)
    logger.info('Search Completed.')


def get_splunk_search_results(session, base_url, sid, params,
                              half_load=False, verify=False):
    '''Gets Splunk results using SID and returns them as a list,
       if half_load is True, return every other search'''
    results_endpoint = '{}/services/search/jobs/{}/results'.format(
                                                            base_url,
                                                            sid)
    logger.info('Getting Splunk search results...')
    response = session.get(results_endpoint, params=params, verify=verify)

    if response.status_code == 200:
        logger.info('Got search results successfully. Extracting results...')
        search_results = response.json()['results']
        logger.info('Extracted {} search results.'.format(len(search_results)))

        if half_load:
            logger.info('half_load enabled, creating new search list...')
            # step two, skipping every other search.
            new_list = search_results[::2]
            logger.info('New search list contains {} items.'.format(
                                                             len(new_list)))
            return new_list

        return search_results

    else:
        raise requests.HTTPError(response)


def generate_summary(results):
    '''Logs summary of executor run'''
    status_codes = []
    wait_secs = []
    for x, y in results:
        status_codes.append(x)
        wait_secs.append(y)

    logger.info('Response code summary: {}'.format(', '.join(
        ['Status({})={}'.format(k, v) for k, v in Counter(
            status_codes).items()]
            )))

    logger.info('Wait time summary: {}'.format(', '.join(
        ['{}s={}'.format(k, v) for k, v in Counter(
            wait_secs).items()]
            )))


if __name__ == '__main__':
    config = configparser.ConfigParser()
    logger.info('Loading config file...')
    config.read('chaoscat.ini')
    logger.info('Config file loaded.')

    # Concurrency
    pool_connections = int(config['concurrency']['pool_connections'])
    pool_maxsize = int(config['concurrency']['pool_maxsize'])
    max_workers = int(config['concurrency']['max_workers'])

    # Splunk
    search_endpoint = config['splunk']['search_endpoint']

    # Azure
    azure_username = config['azure']['username']
    azure_password = config['azure']['password']
    azure_base_url = config['azure']['base_url']
    azure_search_endpoint = '{base}{endpoint}'.format(base=azure_base_url,
                                                      endpoint=search_endpoint)
    # Create an Azure Splunk session
    azure_session = requests.Session()
    # Create and mount adapter with custom connection pool
    adapter = requests.adapters.HTTPAdapter(pool_connections=pool_connections,
                                            pool_maxsize=pool_maxsize,
                                            max_retries=5)
    azure_session.mount('https://', adapter)
    azure_session.auth = (azure_username, azure_password)

    # AWS
    aws_username = config['aws']['username']
    aws_password = config['aws']['password']
    aws_base_url = config['aws']['base_url']
    aws_search_endpoint = '{base}{endpoint}'.format(base=aws_base_url,
                                                    endpoint=search_endpoint)
    # Create an AWS Splunk session
    aws_session = requests.Session()
    aws_session.auth = (aws_username, aws_password)
    aws_results_count = config['aws']['result_count']
    aws_output_mode = 'json'
    aws_results_params = {
        'output_mode': aws_output_mode,
        'count': aws_results_count
        }
    aws_search_data = {
        'search': config['aws']['search_data']
        }

    logger.info('Authenticating with Azure Splunk...')
    azure_auth_response = azure_session.get(azure_base_url, verify=False)
    if azure_auth_response.status_code != 200:
        logger.error('Failed to authenticate with Splunk Azure')
        exit(1)

    logger.info('Authenticating with AWS Splunk...')
    aws_auth_response = aws_session.get(aws_base_url)
    if aws_auth_response.status_code != 200:
        logger.error('Failed to authenticate with Splunk AWS')
        exit(1)

    run_limit = int(config['chaoscat']['run_limit'])
    half_load = bool(int(config['chaoscat']['half_load']))
    run_n = 1
    while True:
        if run_n > run_limit:
            logger.info('Run limit {} reached. Exiting.'.format(run_limit))
            exit(0)
        try:
            logger.info('Starting chaoscat... run number: {}'.format(
                                                                run_n))
            sid = post_splunk_search(aws_session, aws_search_endpoint,
                                     aws_search_data)
            poll_splunk_search(aws_session, aws_search_endpoint, sid)
            search_results = get_splunk_search_results(aws_session,
                                                       aws_base_url,
                                                       sid, aws_results_params,
                                                       half_load=half_load)
            start_datetime_utc = datetime.utcnow()
            logger.info('Starting load test...')
            with concurrent.futures.ThreadPoolExecutor(
                 max_workers=max_workers) as executor:
                    e_results = executor.map(lambda x:
                                             fire_search(azure_session,
                                                         azure_search_endpoint,
                                                         start_datetime,
                                                         x[1], x[0]),
                                             enumerate(search_results)
                                             )
            logger.info('Run {} ended successfully.'.format(run_n))
            generate_summary(e_results)
            # increment run number after a successfull full run
            run_n += 1

        except requests.HTTPError as e:
            logger.error('HTTP ERROR: Restarting... response_code={}, '
                         'response_text={}'.format(
                          e.args[0].status_code, e.args[0].text))
            continue
        except requests.exceptions.ConnectionError as e:
            logger.error('Remote Disconnected: Restarting...  error={}'.format(
                                                                        e))
            continue
        except urllib3.exceptions.ProtocolError as e:
            logger.error('PROTOCOL ERROR: Restarting...  error={}'.format(e))
            continue
