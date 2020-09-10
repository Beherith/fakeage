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
import signal
import sys
import time
import threading
import json
import socket
from SimpleWebSocketServer import WebSocket, SimpleWebSocketServer
from optparse import OptionParser
import http.server
from http.server import SimpleHTTPRequestHandler
import pyqrcode

from unidecode import unidecode #thank me later: https://pypi.org/project/Unidecode/#description
#from gooey import Gooey, GooeyParser

#reload(sys)
#sys.setdefaultencoding('utf8') #https://stackoverflow.com/questions/21129020/how-to-fix-unicodedecodeerror-ascii-codec-cant-decode-byte

class Game:
    def __init__(self):
        self.clients = []  # list of all connected clients
        self.viewers = []  # list of clients who are views only
        self.players = {}  # dict of client:playername
        self.questions = []  # pairs of (questions,answer)

        self.scores = {}  # dict of playername: score
        self.likecount = {}  # dict of playername:numlikes
        self.likes = {}  # dict of playername:likedlie
        self.lies = {}  # dict of playername:submitted lie
        self.choices = {}  # dict of playername:chosen answer
        self.states = ["pregame", "lietome", "lieselection", "scoring", "finalscoring"]

        self.disconnected_players = {} # dict of playername:{'score':0,'likes':0}

        self.question = ''
        self.answer = ''
        self.state = "pregame"
        self.currentlie = None
        self.forcestart = False
        self.t = time.time()
        # some game relevant constants:
        self.autoadvance = False
        self.scoretime = 10  # seconds between each scoring view
        self.lietime = 120  # time each player has to come up with a lie
        self.choicetime = 30  # each player has numlies * choicetime time to select and like answers
        self.questionsperround = 15
        self.roundcount = 0
        self.questionsfilename = ''
        self.scoreorder = []

    def time(self):
        self.t = time.time()

    def loadquestions(self, questionsfilename=''):
        if questionsfilename != '':
            self.questionsfilename = questionsfilename
        #self.questionsfilename = questionsfilename
        questionsfile = open(self.questionsfilename)
        for line in questionsfile.readlines():
            line = line.strip().split('\t')
            if len(line) == 2:
                self.questions.append([line[0], unidecode_allcaps_shorten32(line[1])])
        questionsfile.close()
        print(f'Loaded {len(self.questions)} questions')

    def reset(self):
        self.loadquestions()
        for player in self.scores:
            self.scores[player] = 0
            self.likecount[player] = 0
        self.roundcount = 0
        self.choices = {}
        self.likes = {}
        self.lies = {}
        self.choices = {}

    def addplayer(self, client, playername):
        if len(playername) > 32:
            playername = playername[0:32]
        if playername in iter(self.players.values()):
            print(f'{client} tried to log in as an existing playername {playername}!')
            return -1
        if playername in self.disconnected_players:
            (score, likecount) = self.disconnected_players.pop(playername)
            self.players[client] = playername
            self.scores[playername] = score
            self.likecount[playername] = likecount
            print(f'{playername} reconnected, score = {score}, likecount = {likecount}')
        else:
            self.players[client] = playername
            self.scores[playername] = 0
            self.likecount[playername] = 0
            print(f'{playername} had joined')
        return 0

    def removeplayer(self, client):
        if client in self.clients:
            self.clients.remove(client)
        if client in game.players:
            playername = self.players[client]
            if playername in self.scores and playername in self.likecount:
                self.disconnected_players[playername] = (self.scores[playername], self.likecount[playername])
            if playername in self.lies:
                del self.lies[playername]
            if playername in self.likes:
                del self.likes[playername]
            if playername in self.choices:
                del self.choices[playername]
            del self.players[client]
            if len(self.players) == 0:
                print("Last player left, returning to pregame")
                self.state = 'pregame'
            if playername in self.lies:
                del self.lies[playername]
            if playername in self.lies:
                del self.lies[playername]
        if client in self.viewers:
            self.viewers.remove(client)

    def getgamestate(self):
        gamestatedict = {"state": self.state, 'players': [],
                         "question": self.question,
                         "answer": self.answer,
                         "currentlie": self.currentlie}
        score_sorted_player_list = []
        for playername in self.players.values():
            score_sorted_player_list.append((playername, self.scores[playername]))
        score_sorted_player_list = sorted(score_sorted_player_list, key=lambda tup: (-tup[1], tup[0]))

        for player, _ in score_sorted_player_list:  # what the fuck does this sort on?
            gamestatedict['players'].append({
                'name': player,
                'score': self.scores[player],
                'lie': self.lies[player] if player in self.lies else None,
                'likes': self.likes[player] if player in self.likes else None,
                'likecount': self.likecount[player] if player in self.likecount else 0,
                'choice': self.choices[player] if player in self.choices else None})
        print(f'{len(self.viewers)} viewers, '
              f'{len(self.players)} players, '
              f'viewinfo: {gamestatedict}')
        return gamestatedict

    def updatescoreorder(self):
        self.scoreorder = []  # list of [lie, numtimeselected] lists
        # build a list of lies to score through, and update the scores
        for liername, lie in self.lies.items():  # who chose which lie
            lieselectioncount = 0
            for selectorname, choice in self.choices.items():
                if lie == choice and liername != selectorname:
                    lieselectioncount += 1
                    print(f'Lier: {liername} with lie {lie} got chosen by {selectorname}')
            if lieselectioncount > 0 and (lie, lieselectioncount) not in self.scoreorder:
                self.scoreorder.append((lie, lieselectioncount))
        #for likername, like in self.likes.iteritems():  # who likes which lie
        #     for likedname, likedlie in self.lies.iteritems():
        #         if likername != likedname and like == likedlie:
        #             print likername, 'liked', likedlie, 'by', likedname
        self.scoreorder = sorted(self.scoreorder, key=lambda x: x[1], reverse=True)  # score most chosen answer last
        correctcount = 0
        for _, choice in self.choices.items():
            if choice == self.answer:
                correctcount += 1
        self.scoreorder.append((self.answer, correctcount))  # score truth very last
        print(f'game.scoreorder={self.scoreorder}')
        return self.scoreorder

    def lie_selection_received(self, client, selectedlie):
        if game.state != 'lieselection':
            print(f'{game.players[client]} tried to choose lie {selectedlie} out of time')
            return -1
        player_who_selected_lie = self.players[client]
        if player_who_selected_lie in self.choices:
            print(f'Player {player_who_selected_lie} tried to select another lie '
                  f'({selectedlie}) despite already having chose '
                  f'({self.choices[player_who_selected_lie]})')
            return -1

        if player_who_selected_lie in self.lies and self.lies[player_who_selected_lie] == selectedlie:
            print(f'Player {player_who_selected_lie} tried to select their own lie ({selectedlie})')
            return -1

        self.choices[player_who_selected_lie] = selectedlie

        if selectedlie == self.answer:
            self.scores[player_who_selected_lie] += 1
            print(f'Player {player_who_selected_lie} got the answer '
                  f'({self.answer}) correctly ({selectedlie})')

        for liername, lie in self.lies.items():  # who chose which lie
            if lie == selectedlie and liername != player_who_selected_lie:
                self.scores[liername] += 1
                print(f'Lier {liername} with lie {lie} got chosen by {player_who_selected_lie}')
        self.updatescoreorder()
        return 0

    def like_recieved(self, client, likes):
        if self.state != 'lieselection':
            print(f'{self.players[client]} tried to like lie {likes} out of time')
            return -1
        player_who_liked = self.players[client]
        if player_who_liked in self.likes:
            print(f'Player {player_who_liked} tried to like another submission ({likes}) '
                  f'despite already having chosen ({self.likes[player_who_liked]})')
            return -1

        if player_who_liked in self.lies and likes == self.lies[player_who_liked]:
            print(f'Player {player_who_liked} tried to like their own lie ({likes})')
            return -1

        self.likes[player_who_liked] = likes

        for liername, lie in self.lies.items():  # who chose which lie
            if lie == likes and liername != player_who_liked:
                self.likecount[liername] += 1
                print(f'Player: {player_who_liked} likes {lie} by {liername}')
        return 0

    def submitquestion(self, q_and_a):
        try:
            questionsfile = open(self.questionsfilename, 'a')
            question, _, answer = unidecode(q_and_a).rpartition(':')
            print(f'Question submitted: {q_and_a}')
            questionsfile.write(question + '\t' + unidecode_allcaps_shorten32(answer) + '\n')
        except UnicodeDecodeError:
            print(f'Failed to decode unicode string {q_and_a}')
        except IOError:
            print(f'Failed to open file {self.questionsfilename}')
        finally:
            questionsfile.close()

    def nextquestion(self):
        self.time()
        if not self.questions:
            self.reset()
        (self.question, self.answer) = self.questions.pop(0)

        print(f'The current question and answer are: {self.question} {self.answer}')
        self.choices = {}
        self.likes = {}
        self.lies = {}
        self.choices = {}
        self.roundcount += 1
        self.currentlie = None
        self.state = 'lietome'
        self.scoreorder = []



game = Game()  # this is a global variable, we hope that threading wont fuck it up and the Global interpreter lock helps us


def unidecode_allcaps_shorten32(string):
    tmp = unidecode(string)
    return tmp[0:min(len(tmp), 32)].upper()


def updategameview(recipients='all'):
    global game
    viewinfo = game.getgamestate()

    # unicode is needed cause otherwise JS receives it as a Blob type object instead of string
    ujsonviewinfo = str(json.dumps(viewinfo))
    if recipients in ('all', 'players'):
        for player in game.players:
            player.sendMessage(ujsonviewinfo)
    if recipients in ('all', 'viewers'):
        for viewer in game.viewers:
            viewer.sendMessage(ujsonviewinfo)


def scoring(game):
    print('Scoring called')
    if not game.scoreorder:  # done, advance state
        print('Done with scores, advancing automatically to finalscoring')
        game.state = 'finalscoring'
        updategameview()
        game.time()
        return
    game.currentlie = game.scoreorder.pop(0)[0]
    updategameview()
    game.time()


def handleTick():
    global game
    if game.state == 'pregame':
        if game.forcestart:
            game.forcestart = False
            game.nextquestion()
            updategameview()
        return

    if game.state == 'lietome':
        # total of game.lietime seconds to submit a lie
        # advance automatically if everyone has submitted a lie and liked an answer!
        # if time.time() - game.t > game.lietime or len(game.lies) == len(game.players): #temporarily disable timing
        if len(game.lies) == len(game.players):
            if len(game.lies) == len(game.players):
                print('Everyone has submitted their lie, advancing to lie selection')
            else:
                print('Time to submit lies is up, advancing to lieselection')
            game.time()
            game.state = 'lieselection'
            updategameview()
            return

    if game.state == 'lieselection':
        # numlies*5 + 10 seconds to choose lies and like stuff
        # OR everyone has submitted a choice
        if (game.autoadvance and (time.time() - game.t > (len(game.lies) + 1) * game.choicetime)) or len(game.choices) == len(game.players):
            print('Time to choose answers lies is up, advancing to scoring')
            game.time()
            scoring(game)
            game.state = 'scoring'
            game.t -= game.scoretime  # rewind time to get instant scoring round
            updategameview()
            return

    if game.state == 'scoring':
        if game.autoadvance and (time.time() - game.t > game.scoretime):  # ( 5 if len(game.scoreorder>1) else 10):
            scoring(game)
            return

    if game.state == 'finalscoring':
        if game.autoadvance and time.time() - game.t > 2 * game.scoretime:
            if game.roundcount >= game.questionsperround:
                pass
                game.forcestart = False
                game.reset()
            else:
                game.forcestart = True
            game.state = 'pregame'
            game.time()
            return


class WSFakeageServer(WebSocket):
    def handleMessage(self):
        global game
        print(f'Message from: {self.client} data: {self.data}')
        self.sendMessage(f'Echo: {self.data}')
        if ':' in self.data:
            command, _, parameter = self.data.partition(':')

            if command == 'loginname':
                game.addplayer(self, parameter)
                updategameview('viewers')

            if command == 'forcestart':
                if game.state == 'pregame':
                    game.time()
                    game.forcestart = True
                else:
                    print("Cant force start game in progress!")

            if command == 'view':
                game.viewers.append(self)
                updategameview('viewers')

            if command == 'lie':
                if game.state != 'lietome':
                    print(f'{game.players[self]} tried to submit lie {parameter} out of time')
                else:
                    if game.players[self] in game.lies:
                        print(f'{game.players[self]} tried to lie multiple times!')
                    else:
                        game.lies[game.players[self]] = unidecode_allcaps_shorten32(parameter)
                        updategameview('viewers')

            if command == 'choice':
                if game.lie_selection_received(self, unidecode_allcaps_shorten32(parameter)) >= 0:
                    updategameview('viewers')

            if command == 'like':
                if game.like_recieved(self, unidecode_allcaps_shorten32(parameter)) >= 0:
                    updategameview('viewers')

            if command == 'submitq':
                game.submitquestion(parameter)

            if command == 'advancestate':
                if game.state == 'pregame':
                    print('Force starting through viewer from', game.state)
                    game.time()
                    game.forcestart = True
                elif game.state == "lieselection":
                    game.state = 'scoring'
                    scoring(game)
                elif game.state == 'scoring':
                    scoring(game)
                else:
                    newstate = game.states[max(0, (game.states.index(game.state)+1)%len(game.states))]
                    print(f'Advancing state through viewer: from {game.state} to {newstate}')
                    if newstate == 'pregame':
                        game.forcestart = True
                    game.time()
                    game.state = newstate
                    updategameview()

    def handleConnected(self):
        print(f'{self.address} connected')
        game.clients.append(self)

    def handleClose(self):
        print(f'{self.address} disconnected, removing')
        global game
        game.removeplayer(self)



if __name__ == "__main__":
    parser = OptionParser(usage="usage: %prog [options]", version="%prog 1.0")
    parser.add_option("--host", default='', type='string', action="store", dest="host", help="hostname (localhost)")
    parser.add_option("--httpport", default=8000, type='int', action="store", dest="httpport", help="Http port (8000)")
    parser.add_option("--wsport", default=8001, type='int', action="store", dest="wsport", help="WebSockets port (8001)")
    parser.add_option("--questions", default="questions.tsv", action="store", dest="questions", help="A tab-separated text file with question[tab]answer on each line")
    parser.add_option("--autoadvance", action="store_true", dest="autoadvance", help="Automatically advance game stages")

    (options, args) = parser.parse_args()
    print(f'Options = {options}')

    my_ip = options.host
    if options.host == '':  # automatically generate ws ip
        my_ip = socket.gethostbyname(socket.gethostname())
        print(f'No host ip set, using: {my_ip}')

    myurl = f'http://{my_ip}:{options.httpport}'
    print(f'Server running at: {myurl}')
    myqrcode = pyqrcode.create(myurl)
    myqrcode.png('qrcode.png', scale=6)

    # Set the IP in the js file on each launch of the server.
    # This seems pretty hacky, but i couldnt think of anything better
    websocket_ip_fn = "websocket_ip.js"
    websocket_ip_file = open(websocket_ip_fn)
    websocket_ip_file_text = websocket_ip_file.readlines()
    websocket_ip_file.close()
    websocket_ip_file_text[1] = '   return "ws://{}:{}/"\n'.format(my_ip, str(options.wsport))
    print(websocket_ip_file_text[1])

    websocket_ip_file = open(websocket_ip_fn, 'w')
    websocket_ip_file.write(''.join(websocket_ip_file_text))
    websocket_ip_file.close()

    # load questions:
    game.autoadvance = options.autoadvance

    game.loadquestions(options.questions)

    wsserver = SimpleWebSocketServer(my_ip, options.wsport, WSFakeageServer, selectInterval=0.1)
    wsserver.handleTick = handleTick

    httpserver = http.server.HTTPServer((my_ip, options.httpport), SimpleHTTPRequestHandler)


    def close_sig_handler(signal, frame):  # i wonder what this is for...
        wsserver.close()
        sys.exit()


    signal.signal(signal.SIGINT, close_sig_handler)
    threading.Thread(target=httpserver.serve_forever).start()
    threading.Thread(target=wsserver.serveforever).start()

    print("Servers started.")
    while True:
        time.sleep(0.1)
