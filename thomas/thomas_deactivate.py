#!/usr/bin/env python

import os.path
import argparse
import sys
from email.mime.text import MIMEText
import subprocess
from subprocess import Popen, PIPE
import mysql.connector
from mysql.connector import errorcode
import socket
import validate
import thomas_show
import thomas_utils
import thomas_queries

###############################################################
# Subcommands:
# user, project, projectuser, poc, institute
#
# --debug			show SQL query submitted without committing the change

# custom Action class, must override __call__
class ValidateUser(argparse.Action):
    def __call__(self, parser, namespace, values, option_string=None):
        # raises a ValueError if the value is incorrect
        validate.user(values)
        setattr(namespace, self.dest, values)
# end class ValidateUser

def getargs(argv):
    parser = argparse.ArgumentParser(description="Deactivate entries in the Thomas database. Use [positional argument -h] for more help.")
    # store which subparser was used in args.subcommand
    subparsers = parser.add_subparsers(dest="subcommand")

    # the arguments for subcommand 'user'
    userparser = subparsers.add_parser("user", help="Deactivate a user account")
    userparser.add_argument("-u", "--user", dest="username", help="Username of user", action=ValidateUser)
    userparser.add_argument("--verbose", help="Show SQL queries that are being submitted", action='store_true')
    userparser.add_argument("--debug", help="Show SQL query submitted without committing the change", action='store_true')

    # the arguments for subcommand 'project'
    projectparser = subparsers.add_parser("project", help="Deactivate an entire project")
    projectparser.add_argument("-p", "--project", dest="project_ID", help="The existing project ID", required=True)
    projectparser.add_argument("--verbose", help="Show SQL queries that are being submitted", action='store_true')
    projectparser.add_argument("--debug", help="Show SQL query submitted without committing the change", action='store_true')

    # the arguments for subcommand 'projectuser'
    projectuserparser = subparsers.add_parser("projectuser", help="Deactivate this user's membership in this project")
    projectuserparser.add_argument("-u", "--user", dest="username", help="An existing UCL username", required=True, action=ValidateUser)
    projectuserparser.add_argument("-p", "--project", dest="project_ID", help="An existing project ID", required=True)
    parser.add_argument("--verbose", help="Show SQL queries that are being submitted", action='store_true')
    projectuserparser.add_argument("--debug", help="Show SQL query submitted without committing the change", action='store_true')

    # the arguments for subcommand 'poc'
    pocparser = subparsers.add_parser("poc", help="Deactivate this Point of Contact (only RC Support)")
    pocparser.add_argument("-p", "--poc_id", dest="poc_id", help="Unique PoC ID, in form N(ame)N(ame)_instituteID", required=True)
    pocparser.add_argument("--verbose", help="Show SQL queries that are being submitted", action='store_true')
    pocparser.add_argument("--debug", help="Show SQL query submitted without committing the change", action='store_true')

    # the arguments for subcommand 'institute'
    instituteparser = subparsers.add_parser("institute", help="Deactivate an entire institute/consortium (only RC Support)")
    instituteparser.add_argument("-i", "--id", dest="inst_ID", help="Unique institute ID, eg QMUL, Imperial, Soton", required=True)
    instituteparser.add_argument("--verbose", help="Show SQL queries that are being submitted", action='store_true')
    instituteparser.add_argument("--debug", help="Show SQL query submitted without committing the change", action='store_true')

    # Show the usage if no arguments are supplied
    if len(argv) < 1:
        parser.print_usage()
        exit(1)

    # return the arguments
    # contains only the attributes for the main parser and the subparser that was used
    return parser.parse_args(argv)
# end getargs

# send an email to RC-Support notifying full account deactivation needed,
# unless debugging in which case just print it.
def contact_rc_support(args, request_id):

    body = (args.cluster.capitalize() + """ user deactivation request id """ + str(request_id) + """ has been received.

Please run '""" + args.cluster + """-show requests' on a """ + args.cluster.capitalize() + """ login node to see pending requests.
Requests can then be carried out by running '""" + args.cluster + """-deactivate request id1 [id2 id3 ...]'

""")

    msg = MIMEText(body)
    msg["From"] = "rc-support@ucl.ac.uk"
    msg["To"] = "rc-support@ucl.ac.uk"
    msg["Subject"] = args.cluster.capitalize() + " deactivation request"
    if (args.debug):
        print("")
        print("Email that would be sent:")
        print(msg)
    else:
        p = Popen(["/usr/sbin/sendmail", "-t", "-oi"], stdin=PIPE, universal_newlines=True)
        p.communicate(msg.as_string())
        print("RC Support has been notified to deactivate this account.")
# end contact_rc_support

# everything needed to create a new account creation request
def create_user_request(cursor, args, args_dict):
    # projectusers status is pending until the request is approved
    args_dict['status'] = "pending"
    # add a project-user entry for the user
    cursor.execute(run_projectuser(), args_dict)
    debug_cursor(cursor, args)
    # get the poc_email and add to dictionary
    cursor.execute(run_poc_email(), args_dict)
    poc_email = cursor.fetchall()[0][0]
    args_dict['poc_email'] = poc_email
    # add the account creation request to the database
    cursor.execute(run_addrequest(), args_dict)
    debug_cursor(cursor, args)
# end create_user_request

# everything needed to create a new user
def create_new_user(cursor, args, args_dict):
    # if no username was specified, get the next available mmm username
    if (args.username == None):
        args.username = nextmmm()
    # users status is pending until the request is approved
    args_dict['status'] = "pending"
    # insert new user into users table
    cursor.execute(run_user(args.surname), args_dict)
    debug_cursor(cursor, args)
    # create the account creation request
    create_user_request(cursor, args, args_dict)
# end create_new_user

# Check for duplicate users by key: email or username
def check_dups(key_string, cursor, args, args_dict):
    cursor.execute(thomas_queries.findduplicate(key_string), args_dict)
    results = cursor.fetchall()
    rows_count = cursor.rowcount
    if rows_count > 0:
        # We have duplicate(s). Show results and ask them to pick one or none
        print(str(rows_count) + " user(s) with this " +key_string+ " already exist:\n")
        data = []
        # put the results into a list of dictionaries, keys being db column names.
        for i in range(rows_count):
            data.append(dict(list(zip(cursor.column_names, results[i]))))
            # while we do this, print out the results, numbered.
            print(str(i+1) + ") "+ data[i]['username'] +", "+ data[i]['givenname'] +" "+ data[i]['surname'] +", "+ data[i]['email'] + ", created " + str(data[i]['creation_date']))

        # make a string list of options, counting from 1 and ask the user to pick one
        options_list = [str(x) for x in range(1, rows_count+1)]
        response = thomas_utils.select_from_list("\nDo you want to add a new project to one of the existing accounts instead? \n(You should do this if it is the same individual). \n Please respond with a number in the list or n for none.", options_list)

        # said no to using existing user
        if response == "n":
            # can create a duplicate if it is *not* a username duplicate
            if key_string != "username":
                if thomas_utils.are_you_sure("Do you want to create a second account with that "+key_string+"?"):
                    # create new duplicate user
                    create_new_user(cursor, args, args_dict)
                    return True
                # said no to everything
                else: 
                    print("No second account requested, doing nothing and exiting.")
                    exit(0)
            # Was a username duplicate
            else:
                print("Username in use, doing nothing and exiting.")
                exit(0) 
        # picked an existing user
        else:
            # go back to zero-index, get chosen username
            args.username = data[int(response)-1]['username']
            print("Using existing user " + args.username)
            create_user_request(cursor, args, args_dict) 
            return True

    # there were no duplicates and we did nothing
    return False
# end check_dups

# run all this when someone tries to create a new user
# for now we are assuming the creation request was done on the correct cluster
def new_user(cursor, args, args_dict):

    # if there was no duplicate username check for duplicate email
    if not check_dups("username", cursor, args, args_dict):
        if not check_dups("email", cursor, args, args_dict):
            # no duplicates at all, create new user
            create_new_user(cursor, args, args_dict)

# end new_user

def debug_cursor(cursor, args):
    if (args.verbose or args.debug):
        print(cursor.statement)

# Put main in a function so it is importable.
def main(argv):

    # get the name of this cluster
    nodename = socket.getfqdn()

    # get all the parsed args
    try:
        args = getargs(argv)
        # add cluster name to args
        args.cluster = thomas_utils.getcluster(nodename)
        # make a dictionary from args to make string substitutions doable by key name
        args_dict = vars(args)
    except ValueError as err:
        print(err)
        exit(1)

    if (args.subcommand == "user"):
        # UCL user validation - if this is a UCL email, make sure username was given 
        # and that it wasn't an mmm one.
        validate.ucl_user(args.email, args.username)
        # Unless nosshverify is set, verify the ssh key
        if (args.nosshverify == False):
            validate.ssh_key(args.ssh_key)
            if (args.verbose or args.debug):
                print("")
                print("SSH key verified.")
                print("")

    # connect to MySQL database with write access.
    # (.thomas.cnf has readonly connection details as the default option group)

    try:
        conn = mysql.connector.connect(option_files=os.path.expanduser('~/.thomas.cnf'), option_groups='thomas_update', database='thomas')
        cursor = conn.cursor()

        if (args.verbose or args.debug):
            print("")
            print(">>>> Queries being sent:")

        # cursor.execute takes a querystring and a dictionary or tuple
        if (args.subcommand == "user"):
            new_user(cursor, args, args_dict)

        elif (args.subcommand == "projectuser"):
            # This is an existing user, status for the new project-user pairing is active by default
            args_dict['status'] = "active"
            cursor.execute(run_projectuser(), args_dict)
            debug_cursor(cursor, args)
        elif (args.subcommand == "project"):
            cursor.execute(run_project(), args_dict)
            debug_cursor(cursor, args)
        elif (args.subcommand == "poc"):
            cursor.execute(run_poc(args.surname, args.username), args_dict)
            debug_cursor(cursor, args)
        elif (args.subcommand == "institute"):
            cursor.execute(run_institute(), args_dict)
            debug_cursor(cursor, args)

        # commit the change to the database unless we are debugging
        if (not args.debug):
            if (args.verbose):
                print("")
                print("Committing database change")
                print("")
            conn.commit()

        # Databases are updated, now email rc-support unless nosupportemail is set
        if (args.subcommand == "user" and args.nosupportemail == False):
            # get the last id added (which is from the requests table)
            # this has to be run after the commit
            last_id = cursor.lastrowid
            contact_rc_support(args, last_id)

    except mysql.connector.Error as err:
        if err.errno == errorcode.ER_ACCESS_DENIED_ERROR:
            print("Access denied: Something is wrong with your user name or password")
        elif err.errno == errorcode.ER_BAD_DB_ERROR:
            print("Database does not exist")
        else:
            print(err)
    else:
        cursor.close()
        conn.close()
# end main

# When not imported, use the normal global arguments
if __name__ == "__main__":
    main(sys.argv[1:])
