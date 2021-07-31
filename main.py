# -*- coding: utf-8 -*-
from flask import Flask, request
from subprocess import check_output
from bot import send_release

APP = Flask(__name__)

@APP.route('/', methods=['GET','POST'])
def receive_webhook():
    if request.method == 'GET':
        print(request)
        if request.args.get('hub.challenge', ''):
            return request.args.get('hub.challenge', '')

    elif request.method == 'POST':
        send_release(rawdata=request)

    return '200'

if __name__ == '__main__':
    APP.run(debug=True, port=5600, host='0.0.0.0')
