#!/usr/bin/env python3

import os.path
import sys
import configparser
import argparse
import requests

def getargs(argv):
    parser = argparse.ArgumentParser(description="Update Gold in SAFE.")
    parser.add_argument("--uploadgold", dest="goldstdin", help="Upload Gold balances from stdin, input formed from glsalloc --raw", action='store_true')
    parser.add_argument("--verbose", help="", action='store_true')
    parser.add_argument("--debug", help="", action='store_true')

    # Show the usage if no arguments are supplied
    if len(argv) < 1:
        parser.print_usage()
        exit(1)

    # return the arguments
    # contains only the attributes for the main parser and the subparser that was used
    return parser.parse_args(argv)
# end getargs


# Post the data to SAFE using our credentials
def senddata(config, args, golddata):
    postdata = {'table':'GoldAllocations', 'mode':'upload', 'machine_name':'Thomas', 'update':golddata}
    if args.debug:
        print("Post request would be to " + config['safe']['gold'] + " with data = " + str(postdata))
    else:
        request = requests.post(config['safe']['gold'], auth = (config['safe']['user'], config['safe']['password']), data = postdata)
        if "Total lines:" in request.text:
            print("Gold allocations successfully posted: \n" + request.text)
        else:
            print("Posting to SAFE failed: \n" + request.text)
            exit(1)
# end senddata


# Put main in a function so it is importable.
def main(argv):

    MAX_DATA = 1000

    try:
        args = getargs(argv)
        # make a dictionary from args to make string substitutions doable by key name
        #args_dict = vars(args)
    except ValueError as err:
        print(err)
        exit(1)
    # parse our credentials
    try:
        config = configparser.ConfigParser()
        config.read_file(open(os.path.expanduser('~/.thomas.cnf')))
    except OSError as err:
        print(err)

    # Update Gold allocations from pipe-separated stdin input
    if args.goldstdin:
        # read in chunks of 1000 lines, send
        data = []
        for line in sys.stdin:
            # filter out Faraday allocations
            if "|Faraday" not in line:
                data.append(line)
            if len(data) == MAX_DATA:
                # need the data as one string, not a list of strings
                datastring = "".join(data)
                senddata(config, args, datastring)
                data = []
        # send the leftover chunk of lines
        datastring = "".join(data)
        senddata(config, args, datastring)


# end main

# When not imported, use the normal global arguments
if __name__ == "__main__":
    main(sys.argv[1:])

