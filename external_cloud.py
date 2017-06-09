#!/usr/bin/env python

import logging
try:
    import configargparse
    parseclass = "configargparse"
except ImportError:
    import argparse
    parseclass = "argparse"
import argparse
import urllib
import requests
import hmac
import hashlib
import sys
from struct import *
from time import time
import hmac, hashlib
from base64 import b64decode
from string import maketrans

DEFAULT_LOG_DIR = '/var/log/ejabberd'
URL = ''
SECRET = ''
VERSION = '0.2.1+'

usersafe_encoding = maketrans('-$%', 'OIl')

def send_request(data):
    payload = urllib.urlencode(data)
    signature = hmac.new(SECRET, msg=payload, digestmod=hashlib.sha1).hexdigest();
    headers = {
        'X-JSXC-SIGNATURE': 'sha1=' + signature,
        'content-type': 'application/x-www-form-urlencoded'
    }

    try:
        r = requests.post(URL, data = payload, headers = headers, allow_redirects = False)
    except requests.exceptions.HTTPError as err:
        logging.warn(err)
        return False
    except requests.exceptions.RequestException as err:
        try:
            logging.warn('An error occured during the request: %s' % err)
        except TypeError as err:
            logging.warn('An unknown error occured during the request, probably an SSL error. Try updating your "requests" and "urllib" libraries.')
        return False

    if r.status_code != requests.codes.ok:
        return False

    json = r.json();

    return json;

# First try if it is a valid token
# Failure may just indicate that we were passed a password
def verify_token(username, server, password):
    try:
        token = b64decode(password.translate(usersafe_encoding) + "=======")
    except:
        logging.debug('Could not decode token (maybe not a token?)')
        return False

    jid = username + '@' + server

    if len(token) != 23:
        logging.debug('Token is too short: %d != 23 (maybe not a token?)' % len(token))
        return False

    (version, mac, header) = unpack("> B 16s 6s", token)
    if version != 0:
        logging.debug('Wrong token version (maybe not a token?)')
        return False;

    (secretID, expiry) = unpack("> H I", header)
    if expiry < time():
        logging.debug('Token has expired')
        return False

    challenge = pack("> B 6s %ds" % len(jid), version, header, jid)
    response = hmac.new(SECRET, challenge, hashlib.sha256).digest()

    return hmac.compare_digest(mac, response[:16])

def verify_cloud(username, server, password):
    response = send_request({
        'operation':'auth',
        'username':username,
	'domain':server,
        'password':password
    });

    if not response:
        return False

    if response['result'] == 'success':
        return True

    return False

def is_user_cloud(username, server):
    response = send_request({
        'operation':'isuser',
        'username':username,
	'domain':server
    });

    if not response:
        return False

    if response['result'] == 'success' and response['data']['isUser']:
        return True

    return False

def from_server(type):
    if type == 'ejabberd':
        return from_ejabberd();
    elif type == 'prosody':
        return from_prosody();

def to_server(type, bool):
    if type == 'ejabberd':
        return to_ejabberd(bool);
    elif type == 'prosody':
        return to_prosody(bool);

def from_prosody():
    # "for line in sys.stdin:" would be more concise but adds unwanted buffering
    while True:
        line = sys.stdin.readline()
        if not line:
            break
        line = line.rstrip("\n")
        logging.debug("from_prosody got %s" % line)
        yield line.split(':', 3)

def to_prosody(bool):
    answer = '0'
    if bool:
        answer = '1'
    sys.stdout.write(answer+"\n")
    sys.stdout.flush()

def from_ejabberd():
    length_field = sys.stdin.read(2)
    while len(length_field) == 2:
        (size,) = unpack('>h', length_field)
        if size == 0:
           logging.info("command length 0, treating as logical EOF")
           return
        cmd = sys.stdin.read(size)
        if len(cmd) != size:
           logging.warn("premature EOF while reading cmd: %d != %d" % (len(cmd), size))
           return
        logging.debug("from_ejabberd got %s" % cmd)
        x = cmd.split(':', 3)
        yield x
        length_field = sys.stdin.read(2)

def to_ejabberd(bool):
    answer = 0
    if bool:
        answer = 1
    token = pack('>hh', 2, answer)
    sys.stdout.write(token)
    sys.stdout.flush()

def auth(username, server, password):
    if verify_token(username, server, password):
        logging.info('SUCCESS: Token for %s@%s is valid' % (username, server))
        return True

    if verify_cloud(username, server, password):
        logging.info('SUCCESS: Cloud says password for %s@%s is valid' % (username, server))
        return True

    logging.info('FAILURE: Neither token nor cloud approves user %s@%s' % (username, server))
    return False

def is_user(username, server):
    if is_user_cloud(username, server):
        logging.info('Cloud says user %s@%s exists' % (username, server))
        return True

    return False

def getArgs():
    # build command line argument parser
    desc = '''XMPP server authentication against JSXC>=3.2.0 on Nextcloud.
        See https://jsxc.org or https://github.com/jsxc/xmpp-cloud-auth.'''
    epilog = '''One of -A, -I, and -t is required. If more than
        one is given, -A takes precedence over -I over -t.
        -A and -I imply -d.'''

    if parseclass == "argparse":
        parser = argparse.ArgumentParser(description=desc,
            epilog=epilog)
    else:
	# Config file in /etc or the program directory
        cfpath = sys.argv[0][:-3] + ".conf"
        parser = configargparse.ArgumentParser(description=desc,
            epilog=epilog,
	    default_config_files=['/etc/external_cloud.conf', cfpath])
        parser.add_argument('-c', '--config-file',
            is_config_file=True,
	    help='config file path')

    parser.add_argument('-u', '--url',
        required=True,
        help='base URL')

    parser.add_argument('-s', '--secret',
        required=True,
        help='secure api token')

    parser.add_argument('-l', '--log',
        default=DEFAULT_LOG_DIR,
        help='log directory (default: %(default)s)')

    parser.add_argument('-d', '--debug',
        action='store_true',
        help='enable debug mode')

    parser.add_argument('-t', '--type',
        choices=['prosody', 'ejabberd'],
        help='XMPP server type; implies reading requests from stdin until EOF')

    parser.add_argument('-A', '--auth-test',
	nargs=3, metavar=("USER", "DOMAIN", "PASSWORD"),
        help='single, one-shot query of the user, domain, and password triple')

    parser.add_argument('-I', '--isuser-test',
	nargs=2, metavar=("USER", "DOMAIN"),
        help='single, one-shot query of the user and domain tuple')

    parser.add_argument('--version', action='version', version=VERSION)

    args = parser.parse_args()
    if args.type is None and args.auth_test is None and args.isuser_test is None:
        parser.print_help(sys.stderr)
        sys.exit(1)
    return args.type, args.url, args.secret, args.debug, args.log, args.auth_test, args.isuser_test


if __name__ == '__main__':
    TYPE, URL, SECRET, DEBUG, LOG, AUTH_TEST, ISUSER_TEST = getArgs()

    LOGFILE = LOG + '/extauth.log'
    LEVEL = logging.DEBUG if DEBUG or AUTH_TEST or ISUSER_TEST else logging.INFO

    if not AUTH_TEST and not ISUSER_TEST:
        logging.basicConfig(filename=LOGFILE,level=LEVEL,format='%(asctime)s %(levelname)s: %(message)s')

        # redirect stderr
        ERRFILE = LOG + '/extauth.err'
        sys.stderr = open(ERRFILE, 'a+')
    else:
        logging.basicConfig(stream=sys.stdout,level=LEVEL,format='%(asctime)s %(levelname)s: %(message)s')

    logging.info('Start external auth script %s for %s with endpoint: %s', VERSION, TYPE, URL)
    logging.debug('Log level: %s', 'DEBUG' if LEVEL == logging.DEBUG else 'INFO')

    if ISUSER_TEST:
        success = is_user(ISUSER_TEST[0], ISUSER_TEST[1])
        print(success)
        sys.exit(0)

    if AUTH_TEST:
        success = auth(AUTH_TEST[0], AUTH_TEST[1], AUTH_TEST[2])
        print(success)
        sys.exit(0)

    for data in from_server(TYPE):
        logging.debug('Receive operation ' + data[0]);

        success = False
        if data[0] == "auth" and len(data) == 4:
            success = auth(data[1], data[2], data[3])
        if data[0] == "isuser" and len(data) == 3:
            success = is_user(data[1], data[2])

        to_server(TYPE, success)

    logging.info('Shutting down...');

# vim: tabstop=8 softtabstop=0 expandtab shiftwidth=4 smarttab
