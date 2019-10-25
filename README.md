# KubeRun

This is a proof-of-concept script to be installed on a k8s control node,
intended to work in tandem with a Tapis app runner script to run a compute
task as a Kubernetes pod. The script creates a Kubernetes job configuration
that mounts `$PWD` as `/work`, sets the container `WORKDIR` to that directory,
and runs the task as the user's own uid and gid. It monitors the progress of
the job, printing logs to STDOUT when it completes, deletes the job's pod,
and propagates the container's exit code to the invoking shell.

## Usage

Launch kuberun directly:

```shell
python kuberun.py --x-image ubuntu echo 'Hello, world!'
INFO:root:Launched job.batch/ysij-hkrv-pzxa-dkqw
DEBUG:root:Check status in 2s [0s]
DEBUG:root:Check status in 2s [2s]
DEBUG:root:Check status in 2s [4s]
DEBUG:root:Done
DEBUG:root:Exit code: 0
DEBUG:root:Getting job logs
Hello, world!
DEBUG:root:Cleaning up
```

Emulate launching kuberun from within a Tapis job

```shell
export PATH=$PATH:$PWD
# This is a silent parameter that we use when running other containerized jobs
export CONTAINER_IMAGE=ubuntu
# In the real world, this might be a Tapis job ID
export JOB_ID=$(cat /dev/urandom | env LC_CTYPE=C tr -dc a-z | head -c 40; echo)
bash wrapper.sh.ipcexe
```

Exporting the `CONTAINER_IMAGE` and `JOB_ID` variables makes them available
for Bash variable substitution, which is how Tapis job parameters and
metadata are propagated into the local shell runtime. Note that this setup
runs silently, as outputs are sent to a log file. It also makes sure to
avoid archiving the k8s config file.
