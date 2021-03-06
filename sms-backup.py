#!/usr/bin/env python

# Copyright (c) 2011 Tom Offermann
# 
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the 'Software'), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
# 
# The above copyright notice and this permission notice shall be included in 
# all copies or substantial portions of the Software.
# 
# THE SOFTWARE IS PROVIDED 'AS IS', WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

import csv
import cStringIO
import fnmatch
import json
import logging
import os
import re
import shutil
import sqlite3
import sys
import tempfile

from datetime import datetime

# argparse isn't in standard library until 2.7
try:
    test = argparse.ArgumentParser()
except NameError:
    try:
        import argparse
    except:
        print "argparse required. Try `pip install argparse`."
        sys.exit(1)
        
# silence Python 2.6 buggy warnings about Exception.message
# See: http://code.google.com/p/argparse/issues/detail?id=25
if sys.version_info[:2] == (2, 6):
    import warnings
    warnings.filterwarnings(action='ignore',
                            message="BaseException.message has been "
                                    "deprecated as of Python 2.6",
                            category=DeprecationWarning,
                            module='argparse')

# Global variables
ORIG_DB = 'test.db'
COPY_DB = None

def setup_and_parse(parser):
    """
    Set up ArgumentParser with all options and then parse_args().
    
    Return args.
    """
    parser.add_argument("-q", "--quiet", action='store_true', 
            help="Reduce running commentary.")
    
    # Format Options Group
    format_group = parser.add_argument_group('Format Options')
    format_group.add_argument("-a", "--alias", action="append", 
            dest="aliases", metavar="PHONE=NAME",
            help="Key-value pair (.ini style) that maps a phone "
                 "number to a name. Name replaces phone number in output. Can "
                 "be used multiple times. Optional. If not present, phone "
                 "number is used in output.")
                 
    format_group.add_argument("-d", "--date-format", dest="date_format",
            metavar="FORMAT", default="%Y-%m-%d %H:%M:%S",
            help="Date format string. Optional. Default: '%(default)s'.")
                 
    format_group.add_argument("-f", "--format", dest="format", 
            choices = ['human', 'csv', 'json'], default = 'human', 
            help="How output is formatted. Valid options: 'human' "
                 "(fields separated by pipe), 'csv', or 'json'. "
                 "Optional. Default: '%(default)s'.")
                 
    format_group.add_argument("-m", "--myname", dest="identity", 
            metavar="NAME", default = 'Me',
            help="Name of iPhone owner in output. Optional. "
                 "Default name: '%(default)s'.")
    
    # Output Options Group
    output_group = parser.add_argument_group('Output Options')
    output_group.add_argument("-o", "--output", dest="output", metavar="FILE",
            help="Name of output file. Optional. Default "
                 "(if not present): Output to STDOUT.")
                 
    output_group.add_argument("-p", "--phone", action="append",
            dest="numbers", metavar="PHONE",
            help="Limit output to sms messages to/from this phone number. "
                 "Can be used multiple times. Optional. Default (if "
                 "not present): All messages from all numbers included.")
    
    output_group.add_argument("--no-header", dest="header", 
            action="store_false", default=True, help="Don't print header "
            "row for 'human' or 'csv' formats. Optional. Default (if not "
            "present): Print header row.")
            
    # Input Options Group
    input_group = parser.add_argument_group('Input Options')
    input_group.add_argument("-i", "--input", dest="db_file", metavar="FILE",
            help="Name of SMS db file. Optional. Default: Script will find "
                 "and use db in standard backup location.")
            
    args = parser.parse_args()
    return args

def strip(phone):
    """Remove all non-numeric digits."""
    return re.sub('[^\d]', '', phone)

def format_phone(phone):
    """
    Return consistently formatted phone number for output.
    
    Note: US-centric formatting.
    
    If phone < 10 digits, return stripped phone.
    If phone = 10 digits, return '(555) 555-1212'.
    If phone = 11 digits and 1st digit = '1', return '(555) 555-1212'.
    Otherwise, leave as is.
    """
    ph = strip(phone)
    if len(ph) < 10:
        phone = ph
    elif len(ph) == 10:
        phone = "(%s) %s-%s" % (ph[-10:-7], ph[-7:-4], ph[-4:])
    elif len(ph) == 11 and ph[0] =='1':
        phone = "(%s) %s-%s" % (ph[-10:-7], ph[-7:-4], ph[-4:])
    return phone.decode('utf-8')

def valid_phone(phone):
    """
    Simple validation of phone. It is considered a valid phone number if 
    it has at least 5 digits, after stripping all non-numeric digits.
    
    Returns True if valid, False if not.
    """
    stripped = strip(phone)
    return True if len(stripped) >= 5 else False

def validate_aliases(aliases):
    """Raise exception if any alias is not in 'valid_number = name' format."""
    if aliases:
        for a in aliases:
            # Only one equal sign allowed!
            m = re.search('^([^=]+)=[^=]+$', a)
            if not m:
                raise ValueError("OPTION ERROR: Invalid --alias format. "
                                 "Should be 'number = name'.")
            elif not valid_phone(m.group(1)):
                raise ValueError("OPTION ERROR: Invalid number in --alias.")

def validate_numbers(numbers):
    """Raise exception if invalid phone number found."""
    if numbers:
        for n in numbers:
            if not valid_phone(n):
                raise ValueError("OPTION ERROR: Invalid number in --number.")

def validate(args):
    """
    Make sure aliases and numbers are valid.
    
    If invalid arg found, print error msg and raise exception.
    """
    try:
        validate_aliases(args.aliases)
        validate_numbers(args.numbers)
    except ValueError as err:
        print err, '\n'
        raise

def find_sms_db():
    """Find sms db and return its filename."""
    db_name = '3d0d7e5fb2ce288813306e4d4636395e047a3d28'
    mac_dir = '%s/Library/Application Support/MobileSync' % os.path.expanduser('~')
    paths = []
    for root, dirs, files in os.walk(mac_dir):
        for basename in files:
            if fnmatch.fnmatch(basename, db_name):
                path = os.path.join(root, basename)
                paths.append(path)
    if len(paths) == 0:
        logging.warning("No SMS db found.") 
        path = None
    elif len(paths) == 1:
        path = paths[0]
    else:
        logging.warning("Multiple SMS dbs found.")
        path = None
    return path

def copy_sms_db(db):
    """Copy db to a tmp file, and return filename of copy."""
    try:
        orig = open(db, 'r')
    except:
        logging.error("Unable to open DB file: %s" % db)
        sys.exit(1)
    
    try:
        copy = tempfile.NamedTemporaryFile(delete=False)
    except:
        logging.error("Unable to make tmp file.")
        orig.close()
        sys.exit(1)
        
    try:
        shutil.copyfileobj(orig, copy)
    except:
        logging.error("Unable to copy DB.")
        sys.exit(1)
    finally:
        orig.close()
        copy.close()
    return copy.name

def query_group_ids(phone):
    """
    Find group_ids that match phone in group_member table.
    
    The 'address' field in group_member is inconsistently formatted.
    The same number could be represented as '(555) 555-1212', or
    '+15555551212', or '5555551212'.
    
    To query for matches, we'll perform a LIKE query between the last 
    10 digits of the stripped phone and the stripped address field.
    
    Return list of group_ids.
    """
    ph = strip(phone)
    conn = sqlite3.connect(COPY_DB)
    conn.create_function("STRIP", 1, strip)
    cur = conn.cursor()
    
    query = "select group_id from group_member where STRIP(address) like ?"
    params = ('%'+ph[-10:],)
    cur.execute(query, params)
    result = cur.fetchall()
    conn.close()
    if result:
        group_ids = [r[0] for r in result]
    else:
        group_ids = None
        logging.warning("Phone number not found: %s" % phone)
    return group_ids

def alias_map(aliases):
    """
    Map aliases to group_ids.
    
    For each alias ("number=name"), use number to look up group_ids
    in group_member table.
    
    Return dictionary, where key = group_id, value = alias.
    """
    result = {}
    if aliases:
        for a in aliases:
            m = re.search('^([^=]+)=([^=]+)$', a)
            number = m.group(1)
            name = m.group(2)
            group_ids = query_group_ids(number)
            if group_ids:
                for gid in group_ids:
                    result[gid] = name.decode('utf-8')
    return result

def question_marks_placeholder(num):
    """
    Return comma-separated string of question marks, to 
    be used as a placeholder in the `WHERE IN` clause of query.
    """
    qmarks = []
    for n in range(num):
        qmarks.append("?")
    return ', '.join(qmarks)

def build_msg_query(numbers):
    """
    Build the query for SMS messages.
    
    If `numbers` is not None, that means we're querying for a subset 
    of messages. First, we need to find the group_id associated with 
    each phone number. Then, we select from the message table using a 
    `WHERE IN (list of group_ids)` clause.
    
    If `numbers` is None (or we don't find any group_ids), then we 
    select all messages.
    
    Returns: query (string), params (tuple)
    """
    # Match numbers to group_ids
    group_ids = []
    if numbers:
        for n in numbers:
            gids = query_group_ids(n)
            if gids: 
                group_ids.extend(gids)
            
    if group_ids:
        qmarks = question_marks_placeholder(len(group_ids))
        query = ("select rowid, date, address, text, flags, group_id "
                 "from message "
                 "where group_id in (%s) "
                 "order by rowid" % (qmarks))
        params = tuple(group_ids)
    else:
        query = ("select rowid, date, address, text, flags, group_id "
                 "from message "
                 "order by rowid")
        params = ()
    return query, params

def convert_date(unix_date, format):
    """Convert unix epoch time string to formatted date string."""
    dt = datetime.fromtimestamp(int(unix_date))
    ds = dt.strftime(format)
    return ds.decode('utf-8')

def convert_address(row, me, alias_map):
    """
    Take the address from row (a sqlite3.Row) and return 
    a tuple of address strings: (from_addr, to_addr).
    
    Row only has one address field, but I want address strings for two 
    identities: 'me' and 'other' person I'm texting.
    
    'me' is passed in value.
    
    'other' is either a name from alias_map (if address has alias), 
        OR 'address' as a formatted phone number (default).
        
    'flags' tells us direction of message:
        2 = 'incoming'
        3 = 'outgoing'
    """
    if isinstance(me, str): me = me.decode('utf-8')
    address = format_phone(row['address'])
    if alias_map and row['group_id'] in alias_map:
        other = alias_map[row['group_id']]
    else:
        other = address
        
    if row['flags'] == 2:
        from_addr = other
        to_addr = me
    elif row['flags'] == 3:
        from_addr = me
        to_addr = other
        
    return (from_addr, to_addr)

def skip_row(row):
    """Return True, if row should be skipped."""
    retval = False
    if row['flags'] not in (2, 3):
        logging.info("Skipping msg (%s) not sent. Address: %s. Text: %s." % \
                        (row['rowid'], row['address'], row['text']))
        retval = True
    elif not row['address']:
        logging.info("Skipping msg (%s) without address. "
                        "Text: %s" % (row['rowid'], row['text']))
        retval = True
    elif not row['text']:
        logging.info("Skipping msg (%s) without text. Address: %s" % \
                        (row['rowid'], row['address']))
        retval = True
    return retval

def msgs_human(messages, header):
    """
    Return messages, with optional header row. 
    
    One pipe-delimited message per line in format:
    
    date | from | to | text
    
    Width of 'from' and 'to' columns is determined by widest column value
    in messages, so columns align.
    """
    # Figure out column widths 
    max_from = max([len(x['from']) for x in messages])
    max_to = max([len(x['to']) for x in messages])
    max_date = max([len(x['date']) for x in messages])
    
    from_width = max(max_from, len('From'))
    to_width = max(max_to, len('To'))
    date_width = max(max_date, len('Date'))
    
    msgs = []
    if header:
        htemplate = u"{0:{1}} | {2:{3}} | {4:{5}} | {6}"
        hrow = htemplate.format('Date', date_width, 'From', from_width, 
                               'To', to_width, 'Text')
        msgs.append(hrow)
    for m in messages:
        template = u"{0:{1}} | {2:>{3}} | {4:>{5}} | {6}"
        msg = template.format(m['date'], date_width, m['from'], from_width, 
                              m['to'], to_width, m['text'])
        msgs.append(msg)
    msgs.append('')
    result = '\n'.join(msgs).encode('utf-8')
    return result

def msgs_csv(messages, header):
    """Return messages in .csv format."""
    queue = cStringIO.StringIO()
    writer = csv.writer(queue, dialect=csv.excel, quoting=csv.QUOTE_ALL)
    if header:
        writer.writerow(['Date', 'From', 'To', 'Text'])
    for m in messages:
        writer.writerow([m['date'].encode('utf-8'),
                         m['from'].encode('utf-8'),
                         m['to'].encode('utf-8'),
                         m['text'].encode('utf-8')])
    output = queue.getvalue()
    queue.close()
    return output

def msgs_json(messages, header=False):
    """Return messages in JSON format"""
    output = json.dumps(messages, sort_keys=True, indent=2, ensure_ascii=False)
    return output.encode('utf-8')

def output(messages, out_file, format, header):
    """Output messages to out_file in format."""
    if out_file:
        fh = open(out_file, 'w')
    else:
        fh = sys.stdout
        
    if format == 'human': fmt_msgs = msgs_human
    elif format == 'csv': fmt_msgs = msgs_csv
    elif format == 'json': fmt_msgs = msgs_json
    
    try:
        fh.write(fmt_msgs(messages, header))
    except:
        raise
        
    fh.close()

def main():
    try:
        parser = argparse.ArgumentParser()
        args = setup_and_parse(parser)
        try:
            validate(args)
        except:
            parser.print_help()
            sys.exit(2)     # bash builtins return 2 for incorrect usage.
    
        if args.quiet:
            logging.basicConfig(level=logging.WARNING)
        else:
            logging.basicConfig(level=logging.INFO)
        
        global ORIG_DB, COPY_DB 
        ORIG_DB = args.db_file or find_sms_db()
        COPY_DB = copy_sms_db(ORIG_DB)
        
        aliases = alias_map(args.aliases)
        query, params = build_msg_query(args.numbers)
    
        conn = sqlite3.connect(COPY_DB)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute(query, params)
        logging.info("Run query: %s" % (query))
        logging.info("With query params: (%s)" % (params if params else ''))
    
        messages = []
        for row in cur:
            if skip_row(row): continue
            fmt_date = convert_date(row['date'], args.date_format)
            fmt_from, fmt_to = convert_address(row, args.identity, aliases)
            fmt_text = row['text']
            msg = {'date': fmt_date,
                   'from': fmt_from, 
                   'to': fmt_to,
                   'text': fmt_text}
            messages.append(msg)
        
        conn.close()
        output(messages, args.output, args.format, args.header)
    finally:
        if COPY_DB: 
            os.remove(COPY_DB)
            logging.info("Deleted COPY_DB: %s" % COPY_DB)

    
if __name__ == '__main__':
    main()
