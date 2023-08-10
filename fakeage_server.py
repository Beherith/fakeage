# This Python file uses the following encoding: utf-8
'''
(C) Peter Sarkozy mysterme@gmail.com
Devnotes:
- Send all json as unicode, otherwise it arrives as 'Blob'
- TODO:
	- Prettier UI
	- [x]Experiment with game timings
	- [x]Add manual advance button to game view
	- [x]On viewer panel, also display all the submitted lies too!
	- [x]Handle multiple people saying the same lie
	- [x]Sort scoring order by score!
	- handle websocket IP address!
	- [x] QR code for IP?
	- [x]manual override for master view!
	- feedback when like is pressed!
	- [x]score is also fooked
	- [x] Handle reconnection over the course of a game?
	- [bug] viewer waiting for server not showing joined players.
'''
import argparse
import http.server
import json
import signal
import socket
import sys
import threading
import time

import pyqrcode
from unidecode import unidecode  #thank me later: https://pypi.org/project/Unidecode/#description

from SimpleWebSocketServer import WebSocket, SimpleWebSocketServer


class Singleton(type):
    """ Utility function to implement singleton classes """
    # https://stackoverflow.com/q/6760685
    _instances = {}

    def __call__(cls, *args, **kwargs):
        if cls not in cls._instances:
            cls._instances[cls] = super(
                Singleton, cls).__call__(*args, **kwargs)
        return cls._instances[cls]


class Question:
    def __init__(self, question, answer, likes=None, lies=None, choices=None):
        self.question = question
        self.answer = answer
        # likes,lies and choices dicts use player.name as key
        self.likes = likes or {}
        self.lies = lies or {}
        self.choices = choices or {}

    def as_dict(self):
        return {'question': self.question, 'answer': self.answer}

    def __repr__(self):
        return json.dumps(self.as_dict())

    def remove_player(self, playername):
        self.lies.pop(playername, None)
        self.likes.pop(playername, None)
        self.choices.pop(playername, None)

    def get_player_info(self, playername):
        return {
            'lie': self.lies.get(playername, None),
            'likes': self.likes.get(playername, None),
            'choice': self.choices.get(playername, None),
            }

    def get_scoreorder(self):
        """ Evaluates score order according the received answers/lies """
        scoreorder = []  # list of (lie, numtimeselected)
        tmp_score_set = set()  # maintain a set as well to speed up lookups
        for lier, lie in self.lies.items():
            lieselectioncount = 0
            for selectorname, choice in self.choices.items():
                if lie == choice and lier != selectorname:
                    lieselectioncount += 1
                    print(f'Lier: {lier} with lie {lie} got chosen by {selectorname}')
            lie_tuple = (lie, lieselectioncount)
            if lieselectioncount > 0 and lie_tuple not in tmp_score_set:
                tmp_score_set.add(lie_tuple)
                scoreorder.append(lie_tuple)
        # score most chosen answer last
        scoreorder.sort(key=lambda x: x[1], reverse=True)
        correctcount = sum([1 for _, choice in self.choices.items() if choice == self.answer])
        scoreorder.append((self.answer, correctcount))  # score truth very last
        print(f'scoreorder: {scoreorder}')
        return scoreorder


class Player:
    def __init__(self, name, score=0, likecount=0):
        self.name = name
        self.score = score
        self.likecount = likecount

    def __repr__(self):
        return f'{self.name} (score: {self.score}, likecount: {self.likecount})'

    def reset(self):
        self.score = 0
        self.likecount = 0

    def get_info(self):
        return {
            'name': self.name,
            'score': self.score,
            'likecount': self.likecount,
            }


class Game(metaclass=Singleton):
    def __init__(self):
        # state management
        self.states = ['pregame', 'lietome', 'lieselection', 'scoring', 'finalscoring']
        self.state = 'pregame'

        # players
        self.clients = []  # list of all connected clients
        self.viewers = []  # list of clients who are views only
        self.players = {}  # dict of client:Player
        self.disconnected_players = {} # dict of playername:Player

        # questions
        self.questions = []  # list of Questions
        self.cur_question = None  # current question, read from self.questions
        self.currentlie = None
        self.scoreorder = []

        # internal variables
        self.forcestart = False
        self.roundcount = 0
        self.autoadvance = False
        self.questionsfilename = ''
        self.t = time.time()

        # game config:
        self.questionsperround = 15  # number of question in a game
        self.scoretime = 10  # seconds between each scoring view
        self.lietime = 120  # time [s] each player has to come up with a lie
        self.choicetime = 30  # players have numlies * choicetime seconds to select and like answers

    def time(self):
        self.t = time.time()

    def reset(self):
        # reset questions
        self.questions = []
        self.load_questions()
        # reset players
        for player in self.players.values():
            player.reset()
        # reset game-relevant stuff
        self.roundcount = 0

    def add_player(self, client, playername):
        if len(playername) > 32:
            playername = playername[:32]
        if playername in [p.name for p in self.players.values()]:
            print(f'{client} tried to log in as an existing playername {playername}!')
            return False
        if playername in self.disconnected_players:
            player = self.disconnected_players.pop(playername)
            self.players[client] = player
            print(f'{player} reconnected')
        else:
            self.players[client] = Player(playername)
            print(f'{playername} had joined')
        return True

    def remove_player(self, client):
        if client in self.clients:
            self.clients.remove(client)
        if client in self.viewers:
            self.viewers.remove(client)
        if client in self.players:
            player = self.players[client]
            if player.score > 0  and player.likecount > 0:
                self.disconnected_players[player.name] = player
            if self.cur_question:
                self.cur_question.remove_player(player.name)
            self.players.pop(client, None)
            if not self.players:
                print("Last player left, returning to pregame")
                self.state = 'pregame'

    def get_gamestate(self):
        """ Export current game state in single dictionary """
        question = ''
        answer = ''
        if self.cur_question:
            question = self.cur_question.question
            answer = self.cur_question.answer
        gamestatedict = {
            "state": self.state,
            "players": [],
            "question": question,
            "answer": answer,
            "currentlie": self.currentlie,
        }
        score_sorted_player_list = sorted(self.players.values(),
                                          key=lambda p: (-p.score, p.name))
        for player in score_sorted_player_list:
            player_info = player.get_info()
            if self.cur_question:
                player_info.update(self.cur_question.get_player_info(player.name))
            gamestatedict['players'].append(player_info)
        print(f'{len(self.viewers)} viewers, '
              f'{len(self.players)} players, '
              f'viewinfo: {gamestatedict}')
        return gamestatedict

    def get_player_by_name(self, name):
        return next((p for p in self.players.values() if p.name == name), None)

    def update_view(self, recipients='all'):
        """ Collect and send game state to connected ws clients """
        viewinfo = self.get_gamestate()
        # unicode is needed cause otherwise JS receives
        # it as a Blob type object instead of string
        ujsonviewinfo = json.dumps(viewinfo)
        if recipients in ('all', 'players'):
            for playerclient in self.players:
                playerclient.sendMessage(ujsonviewinfo)
        if recipients in ('all', 'viewers'):
            for viewerclient in self.viewers:
                viewerclient.sendMessage(ujsonviewinfo)

    def do_scoring(self):
        print('Scoring called')
        if not self.scoreorder:  # done, advance state
            print('Done with scores, advancing automatically to finalscoring')
            self.state = 'finalscoring'
        else:
            self.currentlie = self.scoreorder.pop(0)[0]
        self.update_view()
        self.time()

    def lie_selection_received(self, client, selectedlie):
        if self.state != 'lieselection':
            print(f'{self.players[client]} tried to choose lie {selectedlie} out of time')
            return False
        player = self.players[client]
        if player.name in self.cur_question.choices:
            print(f'Player {player.name} tried to select another lie '
                  f'({selectedlie}) despite already having chose '
                  f'({self.cur_question.choices[player.name]})')
            return False

        if self.cur_question.lies.get(player, None) == selectedlie:
            print(f'Player {player} tried to select their own lie ({selectedlie})')
            return False

        self.cur_question.choices[player.name] = selectedlie

        if selectedlie == self.cur_question.answer:
            player.score += 1
            print(f'Player {player.name} got the answer '
                  f'({self.cur_question.answer}) correctly ({selectedlie})')

        for liername, lie in self.cur_question.lies.items():  # who chose which lie
            if lie == selectedlie and liername != player.name:
                lierplayer = self.get_player_by_name(liername)
                if lierplayer:
                    lierplayer.score += 1
                print(f'Lier {liername} with lie {lie} got chosen by {player.name}')
        self.scoreorder = self.cur_question.get_scoreorder()
        return True

    def like_recieved(self, client, likes):
        if self.state != 'lieselection':
            print(f'{self.players[client]} tried to like lie {likes} out of time')
            return False
        player = self.players[client]
        if player.name in self.cur_question.likes:
            print(f'Player {player} tried to like another submission ({likes}) '
                  f'despite already having chosen ({self.cur_question.likes[player]})')
            return False

        if self.cur_question.lies.get(player, None) == likes:
            print(f'Player {player} tried to like their own lie ({likes})')
            return False

        self.cur_question.likes[player.name] = likes

        for liername, lie in self.cur_question.lies.items():  # who chose which lie
            if lie == likes and liername != player.name:
                lierplayer = self.get_player_by_name(liername)
                if lierplayer:
                    lierplayer.likecount += 1
                print(f'Player: {player} likes {lie} by {liername}')
        return True

    def load_questions(self, questionsfilename=''):
        if questionsfilename != '':
            self.questionsfilename = questionsfilename
        with open(self.questionsfilename, 'r', encoding='utf-8') as questionsfile:
            for line in questionsfile.readlines():
                line = line.strip().split('\t')
                if len(line) >= 2:
                    question = Question(line[0], unidecode_allcaps_shorten32(line[1]))
                    self.questions.append(question)
        num_questions = len(self.questions)
        self.questionsperround = min(self.questionsperround, num_questions)
        print(f'Loaded {num_questions} questions')

    def submit_question(self, q_and_a):
        try:
            questionsfile = open(self.questionsfilename, 'a')
            question, _, answer = unidecode(q_and_a).rpartition(':')
            print(f'Question submitted: {q_and_a}')
            questionsfile.write('{}\t{}\n'.format(question, unidecode_allcaps_shorten32(answer)))
        except UnicodeDecodeError:
            print(f'Failed to decode unicode string {q_and_a}')
        except IOError:
            print(f'Failed to open file {self.questionsfilename}')
        finally:
            questionsfile.close()

    def load_next_question(self):
        self.time()
        if not self.questions:
            self.reset()
        self.cur_question = self.questions.pop(0)
        print(f'The current question and answer are: {self.cur_question}')
        self.roundcount += 1
        self.currentlie = None
        self.scoreorder = []

    def handle_state(self, state):
        """ Do actions of a given game state """
        if state in self.states:
            # call specific handler function
            state_handler_func = getattr(self, f'_handle_{state}')
            state_handler_func()

    def _handle_pregame(self):
        if self.forcestart:
            self.forcestart = False
            self.load_next_question()
            self.state = 'lietome'
            self.update_view()

    def _handle_lietome(self):
        # total of game.lietime seconds to submit a lie
        # advance automatically if everyone has submitted a lie and liked an answer!
        # to temporarily disable timing use:
        # if time.time() - game.t > game.lietime or len(game.lies) == len(game.players):
        if len(self.cur_question.lies) == len(self.players):
            print('Everyone has submitted their lie, advancing to lie selection')
            self.time()
            self.state = 'lieselection'
            self.update_view()

    def _handle_lieselection(self):
        # numlies*5 + 10 seconds to choose lies and like stuff
        # OR everyone has submitted a choice
        times_up = (time.time() - self.t) > ((len(self.cur_question.lies) + 1) * self.choicetime)
        everyone_done = len(self.cur_question.likes) == len(self.players)
        if self.autoadvance and times_up or everyone_done:
            print('Time to choose answers lies is up, advancing to scoring')
            self.time()
            self.do_scoring()
            self.state = 'scoring'
            self.t -= self.scoretime  # rewind time to get instant scoring round
            self.update_view()

    def _handle_scoring(self):
        times_up = (time.time() - self.t) > self.scoretime
        if self.autoadvance and times_up:
            self.do_scoring()

    def _handle_finalscoring(self):
        times_up = (time.time() - self.t) > (2 * self.scoretime)
        if self.autoadvance and times_up:
            self.forcestart = True
            if self.roundcount >= self.questionsperround:
                self.forcestart = False
                self.reset()
            self.state = 'pregame'
            self.time()


def unidecode_allcaps_shorten32(string):
    tmp = unidecode(string)
    return tmp[:min(len(tmp), 32)].upper()


def handleTick():
    """ Update Game.

       Called in wsserver.serve_forever() to propagate websocket
       commands to game.
    """
    game.handle_state(game.state)


class WSFakeageServer(WebSocket):
    def handleMessage(self):
        """ Handle incoming ws messages """
        print(f'Message from: {self.client} data: {self.data}')
        self.sendMessage(f'Echo: {self.data}')
        if ':' in self.data:
            command, _, parameter = self.data.partition(':')
            # call specific handler function
            try:
                cmd_handler_func = getattr(self, f'_handle_cmd_{command}')
            except AttributeError:
                print(f'Unsupported command: {command}')
                return
            cmd_handler_func(parameter)

    def _handle_cmd_loginname(self, parameter):
        game.add_player(self, parameter)
        game.update_view('viewers')

    def _handle_cmd_forcestart(self, parameter):
        if game.state == 'pregame':
            game.forcestart = True
        else:
            print("Cant force start game in progress!")

    def _handle_cmd_view(self, parameter):
        game.viewers.append(self)
        game.update_view('viewers')

    def _handle_cmd_lie(self, parameter):
        if game.state != 'lietome':
            print(f'{game.players[self]} tried to submit lie {parameter} out of time')
        else:
            player = game.players[self]
            if player in game.cur_question.lies:
                print(f'{game.players[self]} tried to lie multiple times!')
            else:
                # register lie
                game.cur_question.lies[player.name] = unidecode_allcaps_shorten32(parameter)
                game.update_view('viewers')

    def _handle_cmd_choice(self, parameter):
        if game.lie_selection_received(self, unidecode_allcaps_shorten32(parameter)):
            game.update_view('viewers')

    def _handle_cmd_like(self, parameter):
        if game.like_recieved(self, unidecode_allcaps_shorten32(parameter)):
            game.update_view('viewers')

    def _handle_cmd_submitq(self, parameter):
        game.submit_question(parameter)

    def _handle_cmd_advancestate(self, parameter):
        if game.state == 'pregame':
            print('Force starting through viewer from', game.state)
            game.time()
            game.forcestart = True
        elif game.state == "lieselection":
            game.state = 'scoring'
            game.do_scoring()
        elif game.state == 'scoring':
            game.do_scoring()
        else:
            idx = (game.states.index(game.state) + 1) % len(game.states)
            newstate = game.states[max(0, idx)]
            print(f'Advancing state through viewer: from {game.state} to {newstate}')
            if newstate == 'pregame':
                game.forcestart = True
            game.time()
            game.state = newstate
            game.update_view()

    def handleConnected(self):
        """ Handle new ws connection """
        game.clients.append(self)
        print(f'{self.address} connected')

    def handleClose(self):
        """ Handle a ws connection close """
        game.remove_player(self)
        print(f'{self.address} disconnected, removed')


def write_websocket_ip_to_file(websocket_ip_fn="websocket_ip.js",wshostname = ''):
    """ Write websocket URL to a file """
    ws_ip_file_text = []
    ws_ip_file_text.append(f'//This file is generated by {sys.argv[0]}. '
                           'Do not edit.\n')
    ws_ip_file_text.append('function get_websocket_ip(){\n')
    ws_ip_file_text.append('\treturn "ws://{}:{}/"\n'.format(my_ip if wshostname == '' else wshostname, args.wsport))
    ws_ip_file_text.append('}')
    with open(websocket_ip_fn, 'w') as wsfile_w:
        wsfile_w.write(''.join(ws_ip_file_text))


def close_sig_handler(signum, frame):
    """ Handle close signal (Ctrl+C) """
    wsserver.close()
    httpserver.shutdown()
    sys.exit()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--host",
        type=str,
        default='',
        help="hostname (localhost)",
    )
    parser.add_argument(
        "--wshostname",
        type=str,
        default='',
        help="hostname (localhost)",
    )
    parser.add_argument(
        "--httpport",
        type=int,
        default=8000,
        help="Http port (8000)",
    )
    parser.add_argument(
        "--wsport",
        type=int,
        default=8001,
        help="WebSockets port (8001)",
    )
    parser.add_argument(
        "--questions",
        type=str,
        default="questions.tsv",
        help="A tab-separated text file with question[tab]answer on each line",
    )
    parser.add_argument(
        "--autoadvance",
        action="store_true",
        help="Automatically advance game stages",
    )
    args = parser.parse_args()
    print(f'CLI Arguments: {args}')

    my_ip = args.host
    if args.host == '':  # automatically generate ws ip
        my_ip = socket.gethostbyname(socket.gethostname())
        print(f'No host ip set, using: {my_ip}')

    myurl = f'http://{my_ip}:{args.httpport}'
    print(f'Server running at: {myurl}')

    # generate qr code
    myqrcode = pyqrcode.create(myurl)
    myqrcode.png('qrcode.png', scale=6)

    # Set the IP in the js file on each launch of the server.  This
    # seems pretty hacky, but i couldnt think of anything better
	
	
	
    write_websocket_ip_to_file("websocket_ip.js",args.wshostname)

    game = Game()

    # load questions:
    game.load_questions(args.questions)

    game.autoadvance = args.autoadvance

    # start servers:
    wsserver = SimpleWebSocketServer(my_ip, args.wsport,
                                     WSFakeageServer, selectInterval=0.05)
    wsserver.handleTick = handleTick

    httpserver = http.server.HTTPServer((my_ip, args.httpport),
                                        http.server.SimpleHTTPRequestHandler)

    signal.signal(signal.SIGINT, close_sig_handler)
    threading.Thread(target=httpserver.serve_forever).start()
    threading.Thread(target=wsserver.serveforever).start()

    print("Servers started.")
    while True:
        time.sleep(2)
