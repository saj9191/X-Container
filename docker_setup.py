import argparse
import os
import re
import subprocess
import time

NGINX_CONTAINER_NAME = "nginx_container"
NGINX_MACHINE_PORT = 80
NGINX_CONTAINER_PORT = 80
MEMCACHED_CONTAINER_NAME = "memcached_container"
MEMCACHED_MACHINE_PORT = 11101
MEMCACHED_CONTAINER_PORT = 11211
DOCKER_INSPECT_FILTER = "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}"
XCONTAINER_INSPECT_FILTER = "{{.NetworkSettings.IPAddress}}"

#################################################################################################
# Common functionality
#################################################################################################

def shell_call(command, showCommand=False):
  if showCommand:
    print('RUNNING COMMAND: ' + command)
  p = subprocess.Popen(command, shell=True)
  p.wait()
  if showCommand:
    print('')

def shell_output(command, showCommand=False):
  if showCommand:
    print('RUNNING COMMAND: ' + command)
  output = subprocess.Popen(command, shell=True, stdout=subprocess.PIPE).communicate()[0]
  if showCommand:
    print('')
  return output

def get_known_packages():
  output = shell_output('dpkg --get-selections').decode('utf-8')
  lines = output.split('\n')
  packages = list(map(lambda x: x.split('\t')[0], lines))
  packages = list(filter(lambda x: len(x) > 0, packages))
  return packages

def install(package, known_packages):
  if package in known_packages:
    print(package + " has already been installed")
  else:
    shell_call('apt-get install -y {:s}'.format(package))

def install_common_dependencies(packages):
  shell_call('apt-get update')
  install('linux-tools-4.4.0-92-generic', packages)
  install('make', packages)
  install('gcc', packages)

def get_ip_address(name):
  return shell_output("/sbin/ifconfig {0:s} | grep 'inet addr:' | cut -d: -f2 | awk '{{ print $1 }}'".format(name)).strip()

def setup_nginx_configuration(configuration_file_path):
  configuration = '''
user  nginx;
worker_processes  1;

error_log  /var/log/nginx/error.log warn;
pid        /var/run/nginx.pid;

events {
    worker_connections  1024;
}

http {
    access_log off;
    include       /etc/nginx/mime.types;
    default_type  application/octet-stream;

    sendfile        off; # disable to avoid caching and volume mount issues

    keepalive_timeout  120;

    include /etc/nginx/conf.d/*.conf;
}
'''
  f = open(configuration_file_path, 'w')
  f.write(configuration)
  f.close

def install_benchmark_dependencies(args):
  shell_call("mkdir benchmark")

  if not os.path.exists('XcontainerBolt'):
    shell_call('git clone https://github.coecis.cornell.edu/SAIL/XcontainerBolt.git')

  path = os.getcwd()

  if args.process == 'nginx':
    packages = get_known_packages()
    install('libssl-dev', packages)
    os.chdir('XcontainerBolt/wrk2')
    shell_call('make')
  elif args.process == 'memcached':
    os.chdir('XcontainerBolt/mutated')
    shell_call('git submodule update --init')
    install('dh-autoreconf', packages)
    shell_call('./autogen.sh')
    shell_call('./configure')
    shell_call('make')

  os.chdir(path)

AVG_LATENCY = re.compile('.*Latency +([0-9\.a-z]+)')
TAIL_LATENCY = re.compile('.*99\.999% +([0-9\.a-z]+)')
THROUGHPUT = re.compile('.*Req/Sec +([0-9\.]+)')

def parse_nginx_benchmark(file_name):
  csv_file_name = file_name + ".csv"
  f = open(file_name, "r")

  regex_exps = [AVG_LATENCY, TAIL_LATENCY, THROUGHPUT]
  results = ["N/A"] * 3
  avg_latency = "N/A"
  tail_latency = "N/A"
  throughput = "N/A"

  for line in f.readlines():
    for i in xrange(len(regex_exps)):
      m = regex_exps[i].match(line)
      if m != None:
	results[i] = m.group(1)
        break

  print results
  return results

NANOSECONDS_REGEX = re.compile("([0-9\.]+)us")
MILLISECONDS_REGEX = re.compile("([0-9\.]+)ms")
SECONDS_REGEX = re.compile("([0-9\.]+)s")
MINUTE_REGEX = re.compile("([0-9\.]+)m$")
NOT_AVAILABLE = re.compile("N/A")

def save_benchmark_results(instance_folder, results):
  avg_latency_file = open("{0:s}/avg_latency.csv".format(instance_folder), "w+")
  tail_latency_file = open("{0:s}/tail_latency.csv".format(instance_folder), "w+")
  throughput_file = open("{0:s}/throughput.csv".format(instance_folder), "w+")

  files = [avg_latency_file, tail_latency_file, throughput_file]

  for result in results:
    rate = result[0]
    for i in xrange(len(files)):
      measurement = str(result[1][i])
      m = NOT_AVAILABLE.match(measurement)
      if m != None:
	files[i].write("{0:d},\n".format(rate))
	continue
      for regex in [(0.001, NANOSECONDS_REGEX), (1, MILLISECONDS_REGEX), (1000, SECONDS_REGEX), (60*1000, MINUTE_REGEX)]:
        m = regex[1].match(measurement)
        if m != None:
          measurement = m.group(1)
          measurement = float(measurement) * regex[0]
	  break
      measurement = float(measurement)
      files[i].write("{0:d},{1:0.2f}\n".format(rate, measurement))

def run_nginx_benchmark(args, num_connections, num_threads, duration):
  nginx_folder = "benchmark/nginx-{0:s}".format(args.container)
  shell_call("mkdir {0:s}".format(nginx_folder))
  date = shell_output('date +%F-%H-%M-%S').strip()
  instance_folder = "{0:s}/{1:s}".format(nginx_folder, date)
  shell_call("mkdir {0:1}".format(instance_folder))
  print("Putting NGINX benchmarks in {0:s}".format(instance_folder))

  rates = [1, 10, 25, 50, 75, 100, 150, 200, 250, 300, 400, 500, 750, 1000, 1250, 1500, 1750, 2000, 2250, 2500, 2750, 3000]
  results = []
  for rate in rates:
    benchmark_file = "{0:s}/r{1:d}-t{2:d}-c{3:d}-d{4:d}".format(instance_folder, rate, num_threads, num_connections, duration)
    shell_call('XContainerBolt/wrk2/wrk -R{0:d} -t{1:d} -c{2:d} -d{3:d}s -L http://{4:s}/index.html > {5:s}'
	.format(rate, num_threads, num_connections, duration, args.benchmark_address, benchmark_file), True)
    results.append((rate, parse_nginx_benchmark(benchmark_file)))

  save_benchmark_results(instance_folder, results)

def run_memcached_benchmark(args):
  mutated_folder = 'XContainerBolt/mutated/client/'
  shell_call('{:s}/load_memcache {:s}'.format(mutated_folder, args.benchmark_address))
  shell_call('{:s}/mutated_memcache {:s}'.format(mutated_folder, args.benchmark_address))

def run_benchmarks(args):
  install_benchmark_dependencies(args)
  if args.process == "nginx":
    run_nginx_benchmark(args, 1, 1, args.duration)
  elif args.process == "memcached":
    run_memcached_benchmark(args)

def destroy_container(args):
  if args.container == "docker":
    destroy_docker(args)
  elif args.container == "linux":
    destroy_linux(args)
  elif args.container == "xcontainer":
    destroy_xcontainer(args)
  else:
    raise "destroy_container: Not implemented"

def setup(args):
  if args.container == "docker":
    setup_docker(args)
  elif args.container == "linux":
    setup_linux(args) 
  elif args.container == "xcontainer":
    setup_xcontainer(args)
  else:
    raise "setup: Not implemented for container " + args.container

def setup_port_forwarding(machine_ip, machine_port, container_ip, container_port, bridge_ip):
  shell_call('iptables -I FORWARD -p tcp -d {0:s} -j ACCEPT'.format(container_ip))
  linux_sleep(1)
  shell_call('iptables -I FORWARD -p tcp -s {0:s} -j ACCEPT'.format(container_ip))
  linux_sleep(1)
  shell_call('iptables -I INPUT -m state --state NEW -p tcp -m multiport --dport {0:d} -s 0.0.0.0/0 -j ACCEPT'.format(machine_port))
  linux_sleep(1)
  shell_call('iptables -t nat -I PREROUTING --dst {0:s} -p tcp --dport {1:d} -j DNAT --to-destination {2:s}:{3:d}'.format(machine_ip, machine_port, container_ip, container_port))
  linux_sleep(1)
  shell_call('iptables -t nat -I POSTROUTING -p tcp --dst {0:s} --dport {1:d} -j SNAT --to-source {2:s}'.format(container_ip, container_port, bridge_ip))
  linux_sleep(1)
  shell_call('iptables -t nat -I OUTPUT --dst {0:s} -p tcp --dport {1:d} -j DNAT --to-destination {2:s}:{3:d}'.format(machine_ip, machine_port, container_ip, container_port))
  linux_sleep(1)


#################################################################################################
# X-container specific specific
#################################################################################################
def generate_xcontainer_ip(bridge_ip):
  parts = bridge_ip.split(".")
  last = int(parts[-1])
  new_last = (last + 1) % 255
  parts[-1] = str(new_last)
  return ".".join(parts)

def setup_xcontainer_nginx_container(args):
  setup_docker_nginx_container(XCONTAINER_INSPECT_FILTER, True)
  docker_id = shell_output('docker inspect --format="{{{{.Id}}}}" {0:s}'.format(NGINX_CONTAINER_NAME)).strip()
  bridge_ip = get_ip_address('xenbr0')
  xcontainer_ip = generate_xcontainer_ip(bridge_ip)

  shell_call('docker stop {0:s}'.format(NGINX_CONTAINER_NAME))
  path = os.getcwd()
  os.chdir('/root/experiments/native/compute06/docker')
  machine_ip = get_ip_address('em1')
  setup_port_forwarding(machine_ip, NGINX_MACHINE_PORT, xcontainer_ip, NGINX_CONTAINER_PORT, bridge_ip)
  print 'Setup NGINX X-Container on {0:s}:{1:d}'.format(machine_ip, NGINX_MACHINE_PORT)
  print 'X-Container will take over this terminal....'
  shell_call('python run.py --id {0:s} --ip {1:s} --hvm --name {2:s} --cpu={3:d}'.format(docker_id, xcontainer_ip, NGINX_CONTAINER_NAME, args.cores))

def setup_xcontainer(args):
  if args.process == "nginx":
    setup_xcontainer_nginx_container(args)

def destroy_xcontainer_container(name):
  shell_call("xl destroy {0:s}".format(name))
  shell_call("docker rm {0:s}".format(name))

def destroy_xcontainer(args):
  if args.process == "nginx":
    destroy_xcontainer_container(NGINX_CONTAINER_NAME)
  elif args.process == "memcached":
    destroy_xcontainer_container(MEMCACHED_CONTAINER_NAME)

#################################################################################################
# Docker specific
#################################################################################################
def install_docker_dependencies():
  packages = get_known_packages()
  install_common_dependencies(packages)

  # apt-get may not know about about docker
  if 'docker-ce' in packages:
    print('docker-ce has already been installed')
  else:
    shell_call('curl -fsSL https://download.docker.com/linux/ubuntu/gpg | apt-key add -')
    shell_call('add-apt-repository "deb [arch=amd64] https://download.docker.com/linux/ubuntu xenial stable"')
    shell_call('apt-get update')
    shell_call('apt-get install -y docker-ce')

  if args.benchmark_address == 'benchmark':
    if args.process == 'nginx':
      if not os.path.exists('wrk'):
        shell_call('git clone https://github.com/wg/wrk.git')
        shell_call('make -j4 -C wrk')
    elif args.process == 'memcached':
      if not os.path.exists('XcontainerBolt'):
        shell_call('git clone https://github.coecis.cornell.edu/SAIL/XcontainerBolt.git')
        path = os.getcwd()
        os.chdir('XcontainerBolt/mutated')
        shell_call('git submodule update --init')
        install('dh-autoreconf', packages)
        shell_call('./autogen.sh')
        shell_call('./configure')
        shell_call('make')
        os.chdir(path)

def destroy_docker_container(name):
  shell_call('docker stop ' + name)
  shell_call('docker rm ' + name)

def nginx_docker_port():
  return docker_port(NGINX_CONTAINER_NAME, '([0-9]+)/tcp -> 0.0.0.0:([0-9]+)')

def memcached_docker_port():
  return docker_port(MEMCACHED_CONTAINER_NAME, '([0-9]+)/tcp -> 0.0.0.0:([0-9]+)')

def setup_docker_nginx_container(args, docker_filter, is_xcontainer=False):
  configuration_file_path = '/dev/nginx.conf'
  setup_nginx_configuration(configuration_file_path)

  address = docker_ip(NGINX_CONTAINER_NAME, docker_filter)
  cpu = "--cpus={0:d}".format(args.cores)
  if is_xcontainer:
    cpu = ""
  if address == None:
    shell_call('docker run --name {0:s} -P {1:s} -v {2:s}:/etc/nginx/nginx.conf:ro -d nginx'.format(NGINX_CONTAINER_NAME, cpu, configuration_file_path))
    linux_sleep(5)
    address = docker_ip(NGINX_CONTAINER_NAME, docker_filter)
 
  ports = nginx_docker_port()
  machine_ip = get_ip_address('eno1')
  bridge_ip = get_ip_address('docker0')
  setup_port_forwarding(machine_ip, int(ports[1]), address, int(ports[0]), bridge_ip)
  print("NGINX running on global port {0:s}, container address {1:s}".format(ports[1], address))
  return ports

def setup_docker_memcached_container():
  address = docker_ip(MEMCACHED_CONTAINER_NAME)
  if address == None:
    # TODO: Way to pass in memcached parameters like memory size
    shell_call('docker run --name {:s} -p 0.0.0.0:{:d}:{:d} -d memcached -m 256'
      .format(MEMCACHED_CONTAINER_NAME, MEMCACHED_MACHINE_PORT, MEMCACHED_CONTAINER_PORT)
    )
    address = docker_ip(MEMCACHED_CONTAINER_NAME)

  ports = memcached_docker_port()

  print("memcached running on global port {:s}, container address {:s} port {:s}".format(ports[0], address, ports[1]))
  return ports

def docker_port(name, regex):
  try:
    output = shell_output('docker port {0:s}'.format(name))
    results = re.findall(regex, output)
    if len(results) == 0:
      return None
    else:
      return list(results[0])
  except subprocess.CalledProcessError as e:
    return None

def docker_ip(name, docker_filter):
  try:
    output = shell_output("docker inspect -f '{0:s}' {1:s}".format(docker_filter, name))
    output = output.strip()
    if output == "":
      return None
    return output
  except subprocess.CalledProcessError as e:
    return None

def setup_docker(args):
  install_docker_dependencies()
  if args.process == "nginx":
    setup_docker_nginx_container(args, DOCKER_INSPECT_FILTER)
  elif args.process == "memcached":
    setup_docker_memcached_container()

def destroy_docker(args):
  if args.process == "nginx":
    destroy_docker_container(NGINX_CONTAINER_NAME)
  elif args.process == "memcached":
    destroy_docker_container(MEMCACHED_CONTAINER_NAME)

#################################################################################################
# Linux specific
#################################################################################################

def install_linux_dependencies():
  packages = get_known_packages()
  install_common_dependencies(packages)

  if 'lxc' in packages:
    print('lxc has already been installed')
  else:
    install('lxc', packages)

def linux_container_execute_command(name, command):
  shell_call('lxc-attach --name ' + name + ' -- /bin/sh -c "' + command + '"')

def get_linux_container_ip(name):
  try:
    output = shell_output('lxc-info -n {:s} -iH'.format(name))
    output = output.decode('utf-8').strip()
    if output == "":
      return None
    return output
  except subprocess.CalledProcessError as e:
    return None

def setup_linux(args):
  install_linux_dependencies()
  if args.process == "nginx":
    name = NGINX_CONTAINER_NAME
    container_ip = get_linux_container_ip(name)
    container_port = NGINX_CONTAINER_PORT
    machine_port = NGINX_MACHINE_PORT
    print("container ip", container_ip)
    if container_ip != None:
      return

    setup_linux_nginx_container()
  elif args.process == "memcached":
    name = MEMCACHED_CONTAINER_NAME
    container_ip = get_linux_container_ip(name)
    container_port = MEMCACHED_CONTAINER_PORT
    machine_port = MEMCACHED_MACHINE_PORT

    if container_ip != None:
      return

    setup_linux_memcached_container()
  else:
    raise "setup_linux: Not implemented"

  container_ip = get_linux_container_ip(name)
  shell_call("lxc config set {0:s} limits.cpu {1:d}".format(name, args.cores))
  print("machine port", machine_port, "container ip", container_ip, "container port", container_port)
  machine_ip = get_ip_address('eno1')
  bridge_ip = get_ip_address('lxcbr0')
  setup_port_forwarding(machine_ip, machine_port, container_ip, container_port, bridge_ip)
  print("To benchmark run 'python docker_setup.py -c linux -p nginx -b {0:s}:{1:d}'".format(machine_ip, machine_port))

def start_linux_container(name):
  # TODO: Is this the template we want?
  shell_call('lxc-create --name ' + name + ' -t ubuntu')
  shell_call('lxc-start --name ' + name + ' -d')

def linux_sleep(num_seconds):
  print("Sleeping for {0:d} seconds. Linux container network setup is slow....".format(num_seconds))
  time.sleep(num_seconds)

def setup_linux_nginx_container():
  print("setting up")
  start_linux_container(NGINX_CONTAINER_NAME)
  linux_sleep(5)
  linux_container_execute_command(NGINX_CONTAINER_NAME, 'sudo apt-get update')
  linux_container_execute_command(NGINX_CONTAINER_NAME, 'sudo apt-get install -y nginx') 
  linux_container_execute_command(NGINX_CONTAINER_NAME, 'systemctl status nginx')

def setup_linux_memcached_container():
  start_linux_container(MEMCACHED_CONTAINER_NAME)
  linux_sleep(5)
  linux_container_execute_command(MEMCACHED_CONTAINER_NAME, 'sudo apt-get update')
  linux_container_execute_command(MEMCACHED_CONTAINER_NAME, 'sudo apt-get install -y memcached')
  linux_container_execute_command(MEMCACHED_CONTAINER_NAME, 'memcached -u root &')

def destroy_linux_container(name):
  shell_call('lxc-stop --name ' + name)
  shell_call('lxc-destroy --name ' + name)

def destroy_linux(args):
  if args.process == "nginx":
    destroy_linux_container(NGINX_CONTAINER_NAME)
  elif args.process == "memcached":
    destroy_linux_container(MEMCACHED_CONTAINER_NAME)

#################################################################################################
# Main
#################################################################################################
  
if __name__ == '__main__':
  # Example container setup
  # python3 docker_setup.py -c docker -p nginx

  # Example benchmark setup
  # python docker_setup.py -c docker -p nginx -b 1.2.3.4
  parser = argparse.ArgumentParser()
  parser.add_argument('-c', '--container', help='Indicate type of container (docker, linux)')
  parser.add_argument('-p', '--process', required=True, help='Indicate which process to run on docker (NGINX, Spark, etc)')
  parser.add_argument('-b', '--benchmark_address', type=str, help='Address and port to benchmark (localhost or 1.2.3.4:80)')
  parser.add_argument('-d', '--destroy', action='store_true', default=False, help='Destroy associated container')
  parser.add_argument('--cores', type=int, default=1, help='Number of cores')
  parser.add_argument('--duration', type=int, default=60, help='Benchmark duration')
  args = parser.parse_args()

  if args.benchmark_address != None:
    run_benchmarks(args) 
  elif args.destroy:
    destroy_container(args)
  else:
    setup(args) 
