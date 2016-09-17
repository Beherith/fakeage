# This Python file uses the following encoding: utf-8
'''
(C) Peter Sarkozy mysterme@gmail.com
Devnotes:
- Send all json as unicode, otherwise it arrives as 'Blob'
- TODO:
	- Prettier UI
	- Experiment with game timings
'''
import signal
import sys
import time
import threading
import json
import socket
from SimpleWebSocketServer import WebSocket, SimpleWebSocketServer
from optparse import OptionParser
import BaseHTTPServer
from SimpleHTTPServer import SimpleHTTPRequestHandler

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
		self.states = ["pregame", "lietome", "lieselection", "scoring", "finalscores"]

		self.question = ''
		self.answer = ''
		self.state = "pregame"
		self.currentlie = None
		self.forcestart = False
		self.t = time.time()
		# some game relevant constants:
		self.scoretime = 10  # seconds between each scoring view
		self.lietime = 30  # time each player has to come up with a lie
		self.choicetime = 5  # each player has numlies * choicetime time to select and like answers
		self.questionsperround = 15
		self.roundcount = 0

	def time(self):
		self.t = time.time()

	def loadquestions(self, questionsfilename=''):
		if questionsfilename != '':
			self.questionsfilename = questionsfilename
		self.questionsfilename = questionsfilename
		questionsfile = open(self.questionsfilename)
		for line in questionsfile.readlines():
			line = line.strip().split('\t')
			if len(line) == 2:
				self.questions.append([line[0], cleanupstring(line[1])])
		questionsfile.close()
		print 'Loaded', len(self.questions), 'questions'

	def reset(self):
		self.loadquestions()
		for player in self.scores.iterkeys():
			self.scores[player] = 0
			self.likecount[player] = 0
		self.roundcount = 0


game = Game()  # this is a global variable, we hope that threading wont fuck it up and the Global interpreter lock helps us


def cleanupstring(s):
	s = s[0:min(len(s), 32)].lower()
	# We really dont wish to support some unicode characters, as they interfere with multiple people giving the same answer
	replacedict = {u'á': 'a', u'é': 'e', u'í': 'i', u'ó': 'o', u'ö': 'o', u'ő': 'o', u'ú': 'u', u'ü': 'u', u'ű': 'u'}
	out = ''
	for c in s:
		if c in replacedict:
			out += replacedict[c]
		elif c in ' -' or c.isalnum():
			out += c
	return out.upper()


def updategameview(recipients='all'):
	global game
	viewinfo = {"state": game.state, 'players': [],
				"question": game.question,
				"answer": game.answer,
				"currentlie": game.currentlie}

	for player in sorted(game.players.itervalues()):
		viewinfo['players'].append({
			'name': player,
			'score': game.scores[player],
			'lie': game.lies[player] if player in game.lies else None,
			'likes': game.likes[player] if player in game.likes else None,
			'likecount': game.likecount[player] if player in game.likes else 0,
			'choice': game.choices[player] if player in game.choices else None})

	# Broadcast the game state to all players who have chosen a role :D
	print '%i viewers, %i players, viewinfo: %s' % (len(game.viewers), len(game.players), str(viewinfo))
	# unicode is needed cause otherwise JS receives it as a Blob type object instead of string
	ujsonviewinfo = unicode(json.dumps(viewinfo))
	if recipients == 'all' or recipients == 'players':
		for player in game.players.iterkeys():
			player.sendMessage(ujsonviewinfo)
	if recipients == 'all' or recipients == 'viewers':
		for viewer in game.viewers:
			viewer.sendMessage(ujsonviewinfo)


def handleTick():
	global game
	if game.state == 'pregame':
		if game.forcestart:
			game.forcestart = False
			game.time()
			(game.question, game.answer) = game.questions.pop(0)
			if len(game.questions) == 0:
				game.reset()
			print 'The current question and answer are:', game.question, game.answer
			game.choices = {}
			game.likes = {}
			game.lies = {}
			game.choices = {}
			game.roundcount += 1
			game.state = 'lietome'
			updategameview()
		return

	if game.state == 'lietome':
		# total of game.lietime seconds to submit a lie
		# advance automatically if everyone has submitted a lie and liked an answer!
		if time.time() - game.t > game.lietime or len(game.lies) == len(game.players):
			if len(game.lies) == len(game.players):
				print 'Everyone has submitted their lie, advancing to lie selection'
			else:
				print 'Time to submit lies is up, advancing to lieselection'
			game.time()
			game.state = 'lieselection'
			updategameview()
			return

	if game.state == 'lieselection':
		# numlies*5 + 10 seconds to choose lies and like stuff
		# OR everyone has submitted a choice
		if time.time() - game.t > (len(game.lies) + 1) * game.choicetime or len(game.lies) == len(game.likes):
			print 'Time to choose answers lies is up, advancing to scoring'
			game.time()
			game.scoreorder = []  # list of [lie, numtimeselected] lists
			# build a list of lies to score through, and update the scores
			for liername, lie in game.lies.iteritems():
				lieselectioncount = 0
				for selectorname, choice in game.choices.iteritems():
					if lie == choice and liername != selectorname:
						lieselectioncount += 1
						game.scores[liername] += 1
				if lieselectioncount > 0:
					game.scoreorder.append((lie, lieselectioncount))
			for likername, like in game.likes.iteritems():
				for likedname, likedlie in game.lies.iteritems():
					if likername != likedname and game.currentlie == likedlie:
						game.likecount[likedname] += 1
			game.scoreorder = sorted(game.scoreorder, key=lambda x: x[1], reverse=True) # score most chosen answer last
			correctcount = 0
			for playername, choice in game.choices.iteritems():
				if choice == game.answer:
					game.scores[playername] += 1
					correctcount += 1
			game.scoreorder.append((game.answer, correctcount)) # score truth very last
			print 'game.scoreorder=', game.scoreorder
			game.state = 'scoring'
			game.t -= game.scoretime  # rewind time to get instant scoring round
			# updategameview()
			return

	if game.state == 'scoring':
		if time.time() - game.t > game.scoretime:  # ( 5 if len(game.scoreorder>1) else 10):
			if len(game.scoreorder) == 0:  # done, advance state
				game.state = 'finalscoring'
				updategameview()
				game.time()
				return
			game.currentlie = game.scoreorder.pop(0)[0]
			updategameview()
			game.time()
			return

	if game.state == 'finalscoring':
		if time.time() - game.t > 2 * game.scoretime:
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
		print 'Message from:', self.client, ' data:', self.data
		self.sendMessage("Echo:" + self.data)
		if ':' in self.data:
			cmd = self.data.partition(':')

			if cmd[0] == 'loginname':
				if cmd[2] in game.players.itervalues():
					print self, 'tried to log in as an existing playername!', cmd[2]
					return
				playername = cmd[2]
				game.players[self] = playername
				game.scores[playername] = 0
				game.likecount[playername] = 0
				updategameview('viewers')

			if cmd[0] == 'forcestart':
				if game.state == 'pregame':
					game.t = time.time()
					game.forcestart = True
				else:
					print "Cant force start game in progress!"

			if cmd[0] == 'view':
				game.viewers.append(self)
				updategameview('viewers')

			if cmd[0] == 'lie':
				if game.state != 'lietome':
					print '%s tried to submit lie %s out of time' % (game.players[self], cmd[2])
				else:
					game.lies[game.players[self]] = cleanupstring(cmd[2])
					updategameview('viewers')

			if cmd[0] == 'choice':
				if game.state != 'lieselection':
					print '%s tried to choose lie %s out of time' % (game.players[self], cmd[2])
				else:
					game.choices[game.players[self]] = cmd[2]
					updategameview('viewers')

			if cmd[0] == 'like':
				if game.state != 'lieselection':
					print '%s tried to like lie %s out of time' % (game.players[self], cmd[2])
				else:
					liked = cmd[2]
					game.likes[game.players[self]] = liked
					for player, lie in game.lies.iteritems():
						if lie == liked:
							game.likecount[player] += 1
					updategameview('viewers')
			if cmd[0] == 'submitq':
				cmd = cmd[2].partition(':')
				print 'Question submitted:', cmd
				f = open(game.questionsfile, 'a')
				f.write(cmd[0] + '\t' + cleanupstring(cmd[2]) + '\n')
				f.close()

	def handleConnected(self):
		print (self.address, 'connected')
		game.clients.append(self)

	def handleClose(self):
		global game
		game.clients.remove(self)
		if self in game.players:
			playername = game.players[self]
			if playername in game.lies:
				del game.lies[playername]
			if playername in game.likes:
				del game.likes[playername]
			if playername in game.choices:
				del game.choices[playername]
			del game.players[self]
			if len(game.players) == 0:
				print "Last player left, returning to pregame"
				game.state = 'pregame'
		if self in game.viewers:
			game.viewers.remove(self)
		print (self.address, 'closed')


if __name__ == "__main__":
	parser = OptionParser(usage="usage: %prog [options]", version="%prog 1.0")
	parser.add_option("--host", default='', type='string', action="store", dest="host", help="hostname (localhost)")
	parser.add_option("--port", default=8001, type='int', action="store", dest="port", help="port (8001)")
	parser.add_option("--questions", default="questions.tsv", action="store", dest="questions", help="A tab-separated text file with question[tab]answer on each line")

	(options, args) = parser.parse_args()
	print "Options = ", options

	my_ip = '127.0.0.1'
	if options.host == '':  # automatically generate ws ip
		my_ip = socket.gethostbyname(socket.gethostname())
		print 'No host ip set, using:', my_ip
	else:
		my_ip = options.host

	# Set the ip in the js file on each launch of the server. This seems pretty hacky, but i couldnt think of anything better
	websocket_ip_fn = "websocket_ip.js"
	websocket_ip_file = open(websocket_ip_fn)
	websocket_ip_file_text = websocket_ip_file.readlines()
	websocket_ip_file.close()
	websocket_ip_file_text[1] = '	return "ws://' + my_ip + ':' + str(options.port) + '/"\n'
	websocket_ip_file = open(websocket_ip_fn, 'w')
	websocket_ip_file.write(''.join(websocket_ip_file_text))
	websocket_ip_file.close()

	# load questions:
	game.loadquestions(options.questions)

	print "Testing unicode cleanup:", cleanupstring(u"Búzafűlé	\tcsicskalangos CleanUpString tesztelÉs")

	wsserver = SimpleWebSocketServer(my_ip, options.port, WSFakeageServer, selectInterval=0.1)
	wsserver.handleTick = handleTick

	httpserver = BaseHTTPServer.HTTPServer((my_ip, 8000), SimpleHTTPRequestHandler)


	def close_sig_handler(signal, frame):  # i wonder what this is for...
		wsserver.close()
		sys.exit()


	signal.signal(signal.SIGINT, close_sig_handler)
	threading.Thread(target=httpserver.serve_forever).start()
	threading.Thread(target=wsserver.serveforever).start()

	print "Servers started."
	while (1):
		time.sleep(0.1)
