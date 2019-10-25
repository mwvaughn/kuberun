#!/usr/bin/env python3

import argparse
import datetime
import json
import logging
import os
import random
import string
import subprocess
import sys
import time
import collections
from subprocess import CalledProcessError

KUBECTL = 'kubectl'
FILENAME = 'ipcexe.yml'
# 24 hours
MAXTIME = 86400
RETRY = 2
JOB_STATUSES = ('waiting', 'running', 'terminated')
TEMPLATE = '''---
apiVersion: batch/v1
kind: Job
metadata:
  name: {0}
spec:
  template:
    spec:
      containers:
        - name: {0}
          image: {1}
          command: [{5}]
          workingDir: /work
          volumeMounts:
            - name: cwd
              mountPath: /work
      restartPolicy: Never
      securityContext:
        runAsUser: {2}
        runAsGroup: {3}
      volumes:
        - name: cwd
          hostPath:
            path: {4}
  backoffLimit: 0
'''

JobStatus = collections.namedtuple(
    'JobStatus', 'state exit_code started_at finished_at reason message')


def check_programs(programs):
    progs = []
    if not isinstance(programs, list):
        progs = [programs]
    else:
        progs = programs

    for prog in progs:
        try:
            subprocess.check_output('which {0}'.format(prog), shell=True)
        except CalledProcessError:
            raise IOError('{0} not found'.format(prog))


def random_string(size=32, chars=string.ascii_lowercase):
    return ''.join(random.choice(chars) for x in range(size))


def generate_job_name(fields=4):
    """Generate a random, valid Kubernetes job name
    """
    name_fields = []
    for f in range(fields):
        name_fields.append(random_string(4))
    return '-'.join(name_fields)


def template_config(job_name,
                    container_repo,
                    filename,
                    workdir=None,
                    uid=None,
                    gid=None,
                    args=[]):
    """Generate a Kubernetes job config file
    """
    args = ['"{0}"'.format(a) for a in args]
    args_str = ', '.join(args)
    if uid is None:
        uid = os.getuid()
    if gid is None:
        gid = os.getgid()
    if workdir is None:
        workdir = os.getcwd()

    with open(filename, 'w') as f:
        f.write(
            TEMPLATE.format(str(job_name), container_repo, uid, gid, workdir,
                            args_str))
        f.close()


def schedule_job(jobfile=FILENAME):
    """Schedule the job
    """
    try:
        pod_name = subprocess.check_output('{0} apply -f {1}'.format(
            KUBECTL, jobfile),
            shell=True)
        pod_name = pod_name.decode('utf-8')
        pod_name = pod_name.replace(' created', '')
        pod_name = pod_name.strip()
        return pod_name
    except CalledProcessError:
        raise


def get_job_state(job_name):
    get_pod = '{0} get pods --selector=job-name={1} --output=json'.format(
        KUBECTL, job_name)

    json_resp = subprocess.check_output(get_pod, shell=True)
    job_state = {}
    try:
        job_state = json.loads(json_resp.decode('utf-8'))
    except Exception:
        raise

    state_obj = job_state.get('items',
                              [])[0].get('status',
                                         {}).get('containerStatuses',
                                                 [])[0].get('state', {})

    state_name = list(state_obj.keys())[0]
    if state_name not in JOB_STATUSES:
        raise ValueError('Unknown state encountered: {0}'.format(state_name))

    state_record = state_obj[state_name]
    response = [state_name]
    for k in ('exitCode', 'startedAt', 'finishedAt', 'reason', 'message'):
        response.append(state_record.get(k, None))

    decoded_response = []
    for r in response:
        try:
            val = r.decode('utf-8')
        except Exception:
            val = r
        decoded_response.append(val)
    response = JobStatus(*decoded_response)
    return response


def get_pod_logs(pod_name, tail=None):
    """Fetch pod logs
    """
    get_logs = '{0} logs {1}'.format(KUBECTL, pod_name)
    if isinstance(tail, int):
        get_logs = get_logs + ' --tail={0}'.format(tail)
    try:
        logs = subprocess.check_output(get_logs, shell=True)
        # TODO - wrap in case Unicode decode fails
        logs = logs.decode('utf-8').strip()
        return logs
    except CalledProcessError:
        raise


def seconds():
    return int(datetime.datetime.now().timestamp())


def delete_job(job_name, force=False):
    """Delete a job, usually after it executes or fails
    """
    do_delete = '{0} delete {1} {2}'.format(KUBECTL, 'job.batch', job_name)
    if force:
        do_delete = do_delete + ' --force --grace-period=0'
    try:
        subprocess.check_output(do_delete, shell=True)
    except CalledProcessError:
        raise


def monitor_job(job_name, timeout):
    """Poll job and return exit code
    """
    if timeout is None:
        timeout = MAXTIME
    if timeout > MAXTIME:
        timeout = MAXTIME

    start_time = seconds()
    elapsed_time = 0
    exit_code = None
    job_state = None
    while exit_code is None and elapsed_time < timeout:
        get_job_state(job_name)
        job_state = get_job_state(job_name)
        exit_code = job_state.exit_code
        logging.debug('Check {0} status in {1}s [{2}s]'.format(
            job_name, RETRY, elapsed_time))
        time.sleep(RETRY)
        elapsed_time = seconds() - start_time
    if exit_code is not None:
        logging.debug('Done'.format(RETRY))
        return job_state
    else:
        raise SystemError('Runtime exceeded (limit: {}s)'.format(timeout))


def main(args, unknownargs):

    logging.basicConfig(level=logging.DEBUG)

    # Ensure binaries are present
    check_programs(KUBECTL)

    if args.job is None:
        job_name = generate_job_name()
    else:
        job_name = args.job

    if args.filename is None:
        filename = FILENAME
    else:
        filename = args.filename

    try:
        # Template ipcexe.yml file
        template_config(job_name, args.image, filename, args=unknownargs)
        # Schedule job
        pod_name = schedule_job(filename)
        logging.info('Launched {0}'.format(pod_name))

        # Monitor job
        job_state = monitor_job(job_name, args.timeout)
        logging.debug('Started: {0}'.format(job_state.started_at))
        logging.debug('Finished: {0}'.format(job_state.finished_at))
        logging.debug('Message: {0}'.format(job_state.message))
        logging.debug('Reason: {0}'.format(job_state.reason))
        logging.debug('Exit code: {0}'.format(job_state.exit_code))

        logging.debug('Getting job logs')
        print(get_pod_logs(pod_name, tail=args.tail))

        # Delete pod
        if args.no_cleanup is not True:
            logging.debug('Cleaning up')
            delete_job(job_name)

        # Propagate exit code
        sys.exit(job_state.exit_code)

    except Exception as e:
        logging.exception(e)
        sys.exit(1)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description='Run a task in a Kubernetes pod')
    parser.add_argument('--x-image',
                        dest='image',
                        metavar='image[:tag]',
                        default='ubuntu:cosmic',
                        help='Container repo')
    parser.add_argument('--x-job',
                        dest='job',
                        metavar='<job-name>',
                        help='Job name (must be distinct)')
    parser.add_argument('--x-filename',
                        dest='filename',
                        metavar='<filename.yml>',
                        help='Job filename')
    parser.add_argument('--x-timeout',
                        dest='timeout',
                        metavar='<seconds>',
                        help='Maximum run time')
    parser.add_argument('--x-tail',
                        dest='tail',
                        metavar='<lines>',
                        help='Return last n log lines')
    parser.add_argument('--x-no-cleanup',
                        dest='no_cleanup',
                        action='store_true',
                        help='Do not clean up Job when finished')
    args, unknownargs = parser.parse_known_args()
    main(args, unknownargs)
