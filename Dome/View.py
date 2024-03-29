from __future__ import nested_scopes

try:
	import rox
	from rox import alert, g
except:
	def alert(message):
		print "***", message

import support
from support import *
from xml.dom import Node, XMLNS_NAMESPACE
from Ft.Xml import XPath
from Ft.Xml.XPath import FT_EXT_NAMESPACE, Context
from Ft.Xml.cDomlette import implementation
from Ft.Xml.Domlette import PrettyPrint

import re, string, sys
import urlparse
from StringIO import StringIO

from Program import Op, Block
from Beep import Beep

import time
import urllib, urllib2
import traceback

from constants import *

import re

#http://www.w3.org/2001/12/soap-envelope'
SOAPENV_NS = 'http://schemas.xmlsoap.org/soap/envelope/'

def elements(node):
	out = []
	for x in node.childNodes:
		if x.nodeType == Node.ELEMENT_NODE:
			out.append(x)
	return out

normal_chars = string.letters + string.digits + "-"

fast_global = re.compile('//([-A-Za-z][-A-Za-z0-9]*:)?[-A-Za-z][-A-Za-z0-9]*$')

# An view contains:
# - A ref to a DOM document
# - A set of current nodes
# - A root node
# - A chroot stack
# It does not have any display code. It does contain code to perform actions
# (actions affect the document AND the view state).

# These actions can be repeated using '.'
record_again = [
	"do_global",
	"select_children",
	"subst",
	"python",
	"split",
	"xpath",
	"ask",
	"yank",
	"shallow_yank",
	"delete_node",
	"move_selection",
	"delete_node_no_clipboard",
	"delete_shallow",
	"play",
	"map",
	"change_node",
	"add_node",
	"suck",
	"http_post",
	"put_before",
	"put_after",
	"put_replace",
	"put_as_child",
	"put_as_child_end",
	"yank_value",
	"yank_attribs",
	"paste_attribs",
	"compare",
	"fail",
	"assert_xpath",
	"do_pass",
	"attribute",
	"set_attrib",
	"rename_attrib",
	"add_attrib",
	"soap_send",
	"show_canvas",
	"show_html",
	"select_dups",
	"select_region",
]

def same(a, b):
	"Recursivly compare two nodes."
	if a.nodeType != b.nodeType or a.nodeName != b.nodeName:
		return FALSE
	if a.nodeValue != b.nodeValue:
		return FALSE
	aks = a.childNodes
	bks = b.childNodes
	if len(aks) != len(bks):
		return FALSE
	for (ak, bk) in map(None, aks, bks):
		if not same(ak, bk):
			return FALSE
	return TRUE

class InProgress(Exception):
	"Throw this if the operation will complete later..."
class Done(Exception):
	"Thrown when the chain is completed successfully"

class View:
	def __init__(self, model, callback_handlers = None):
		"""callback_handlers is an (idle_add, idle_remove) tuple"""
		self.root = None
		self.displays = []
		self.lists = []
		self.single_step = 1	# 0 = Play   1 = Step-into   2 = Step-over
		self.model = None
		self.chroots = []	# (model, node, marked)
		self.foreach_stack = []	# (block, [nodes], restore-nodes, restore-marks)
		self.current_nodes = []
		self.clipboard = None
		self.current_attrib = None
		self.marked = {}
		
		if not callback_handlers:
			from rox import g
			self.idle_add, self.idle_remove = g.idle_add, g.idle_remove
		else:
			self.idle_add, self.idle_remove = callback_handlers

		self.exec_point = None		# None, or (Op, Exit)
		self.rec_point = None		# None, or (Op, Exit)
		self.op_in_progress = None
		self.idle_cb = 0
		self.callback_on_return = None	# Called when there are no more Ops...
		self.in_callback = 0		# (not the above callback - this is the playback one)
		self.innermost_failure = None
		self.call_on_done = None	# Called when there is nowhere to return to
		self.exec_stack = []		# Ops we are inside (display use only)

		self.breakpoints = {}		# (op, exit) keys => start-recording?
		self.current_nodes = []
		self.set_model(model)

	def get_current(self):
		if len(self.current_nodes) == 1:
			return self.current_nodes[0]
		raise Exception('This operation required exactly one selected node!')
		
	def set_model(self, model):
		assert not self.marked
		if self.root:
			self.move_to([])
			self.model.unlock(self.root)
		self.root = None
		if self.model:
			self.model.remove_view(self)
			self.model.root_program.watchers.remove(self)
		self.model = model
		self.model.root_program.watchers.append(self)
		model.add_view(self)
		self.set_display_root(self.model.get_root())
		self.move_to(self.root)
	
	def running(self):
		return self.idle_cb != 0 or self.in_callback

	def run_new(self, callback = None):
		"Reset the playback system (stack, step-mode and point)."
		"Call callback(exit) when execution finishes."
		if self.idle_cb:
			self.idle_remove(self.idle_cb)
			self.idle_cb = 0
		self.single_step = 0
		self.innermost_failure = None
		self.call_on_done = callback
		self.callback_on_return = None
		while self.exec_stack:
			self.pop_stack()
		self.reset_foreach_stack()
		self.status_changed()
		self.update_stack()
		self.set_status(None)
	
	def reset_foreach_stack(self):
		for block, nodes, restore, mark in self.foreach_stack:
			if mark:
				print "reset_foreach_stack: unlocking %d nodes" % len(mark)
				[self.model.unlock(x) for x in mark]
		self.foreach_stack = []

	def push_stack(self, op):
		if not isinstance(op, Op):
			raise Exception('push_stack: not an Op', op)
		self.exec_stack.append(op)
		self.update_stack(op)

	def pop_stack(self):
		op = self.exec_stack.pop()
		self.update_stack(op)
	
	def update_stack(self, op = None):
		"Called when exec_stack or foreach_stack changes."
		for l in self.lists:
			l.update_stack(op)

	def set_exec(self, pos):
		if self.op_in_progress:
			raise Exception("Operation in progress...")
		if pos is not None:
			assert isinstance(pos[0], Op)
			assert pos[1] in ['next', 'fail']
		self.exec_point = pos
		#if pos:
		#print "set_exec: %s:%s" % pos
		for l in self.lists:
			l.update_points()

	def set_rec(self, pos):
		self.rec_point = pos
		for l in self.lists:
			l.update_points()
		self.status_changed()
	
	def record_at_point(self):
		if not self.exec_point:
			alert("No current point!")
			return
		self.set_rec(self.exec_point)
		self.set_exec(None)

	def stop_recording(self):
		if self.rec_point:
			self.set_exec(self.rec_point)
			self.set_rec(None)
		else:
			alert("Not recording!")

	def may_record(self, action):
		"Perform and, possibly, record this action"
		rec = self.rec_point

		if rec:
			print "RECORD:", rec, action
			(op, old_exit) = rec
			if action == ['enter']:
				new_op = Block(op.parent)
				new_op.toggle_enter()
				if len(self.current_nodes) > 1:
					new_op.toggle_foreach()
			else:
				new_op = Op(action)
			op.link_to(new_op, old_exit)
			self.set_exec(rec)
			try:
				self.do_one_step()
			except InProgress:
				if isinstance(new_op, Block):
					self.set_rec((new_op.start, 'next'))
				else:
					self.set_rec((new_op, 'next'))
				return
			play_op, exit = self.exec_point
			# (do_one_step may have stopped recording)
			if self.rec_point:
				self.set_rec((new_op, exit))
				self.set_exec(None)
			return

		exit = 'next'
		try:
			self.do_action(action)
		except InProgress:
			pass
		except Beep, b:
			from rox import g
			g.gdk.beep()
			self.set_status(str(b))
			#(type, val, tb) = sys.exc_info()
			#if not val.may_record:
			#	return 0
			exit = 'fail'
		except Done:
			raise
		except:
			rox.report_exception()
	
	def add_display(self, display):
		"Calls move_from(old_node) when we move and update_all() on updates."
		self.displays.append(display)
		#print "Added:", self.displays
	
	def remove_display(self, display):
		self.displays.remove(display)
		#print "Removed, now:", self.displays
		if not self.displays:
			self.delete()
	
	def update_replace(self, old, new):
		if old == self.root:
			self.root = new
		if old in self.current_nodes:
			self.model.lock(new)
			self.model.unlock(old)
			self.current_nodes.remove(old)
			self.current_nodes.append(new)
			self.update_all(new.parentNode)
		else:
			self.update_all(new.parentNode)
		
	def has_ancestor(self, node, ancestor):
		while node != ancestor:
			node = node.parentNode
			if not node:
				return FALSE
		return TRUE
	
	def update_all(self, node):
		for display in self.displays:
			display.update_all(node)
	
	def delete(self):
		#print "View deleted"
		self.model.root_program.watchers.remove(self)
		self.move_to([])
		for l in self.lists:
			l.destroy()
		self.model.unlock(self.root)
		self.root = None
		self.model.remove_view(self)
		self.model = None
	
	# 'nodes' may be either a node or a list of nodes.
	# (duplicates will be removed)
	# If it's a single node, then an 'attrib' node may also be specified
	def move_to(self, nodes, attrib = None):
		if self.current_nodes == nodes:
			return

		if attrib and attrib.nodeType != Node.ATTRIBUTE_NODE:
			raise Exception('attrib not of type ATTRIBUTE_NODE!')

		if type(nodes) != list:
			assert nodes
			nodes = [nodes]
		else:
			for n in nodes: assert n.nodeType

		if len(nodes) > 1:
			# Remove duplicates
			map = {}
			old = nodes
			nodes = []
			for n in old:
				if n not in map:
					map[n] = None
					nodes.append(n)
			#if len(old) != len(nodes):
			#	print "(move_to: attempt to set duplicate nodes)"

		old_nodes = self.current_nodes
		self.current_nodes = nodes

		for node in self.current_nodes:
			self.model.lock(node)
		for node in old_nodes:
			self.model.unlock(node)

		self.current_attrib = attrib

		for display in self.displays:
			display.move_from(old_nodes)
	
	def move_prev_sib(self):
		try:
			new = [n.previousSibling or 1/0 for n in self.current_nodes]
		except:
			raise Beep
		self.move_to(new)
	
	def move_next_sib(self):
		try:
			new = [n.nextSibling or 1/0 for n in self.current_nodes]
		except:
			raise Beep
		self.move_to(new)
	
	def move_left(self):
		new = []
		for n in self.current_nodes:
			if n == self.root:
				raise Beep
			p = n.parentNode
			if p not in new:
				new.append(p)
		self.move_to(new)
	
	def move_right(self):
		new = []
		for n in self.current_nodes:
			kids = n.childNodes
			if kids:
				new.append(kids[0])
			else:
				raise Beep
		self.move_to(new)
	
	def move_home(self):
		self.move_to(self.root)
	
	def move_end(self):
		if not self.get_current().childNodes:
			raise Beep
		node = self.get_current().childNodes[0]
		while node.nextSibling:
			node = node.nextSibling
		self.move_to(node)
	
	def set_display_root(self, root):
		self.model.lock(root)
		if self.root:
			self.model.unlock(self.root)
		self.root = root
		self.update_all(root)
	
	def enter(self):
		"""Change the display root to a COPY of the selected node.
		Call Leave to check changes back in."""
		node = self.get_current()
		if node is self.root:	# Locking problems if this happens...
			raise Beep("Can't enter the root node")
		if node.nodeType != Node.ELEMENT_NODE:
			raise Exception('Can only enter an element!')
		if self.model.doc is not node.ownerDocument:
			raise Exception('Current node not in view!')
		self.move_to([])
		self.set_marked([])

		new_model = self.model.lock_and_copy(node)
		self.chroots.append((self.model, node, self.marked))
		self.set_model(new_model)
		self.update_stack()
	
	def leave(self):
		"""Undo the effect of the last chroot()."""
		if not self.chroots:
			raise Beep

		self.set_marked([])
		self.move_to([])
		model = self.model

		(old_model, old_node, old_marked) = self.chroots.pop()
		self.update_stack()

		copy = old_model.import_with_ns(self.model.get_root())
		old_model.unlock(old_node)
		old_model.replace_node(old_node, copy)
		self.set_model(old_model)
		self.move_to([copy])
		self.set_marked(old_marked.keys())

		if not model.views:
			model.undo_stack = None
			model.__dict__ = {}
			del model
			import gc
			gc.collect()

	def do_action(self, action):
		"'action' is a tuple (function, arg1, arg2, ...)"
		"Performs the action. Returns if action completes, or raises "
		"InProgress if not (will call resume() later)."
		if action[0] in record_again:
			self.last_action = action
		elif action[0] == 'again':
			action = self.last_action
		fn = getattr(self, action[0])
		exit = 'next'
		#print "DO:", action[0]
		self.model.mark()
		try:
			new = apply(fn, action[1:])
		except InProgress:
			raise
		except Beep:
			if not self.op_in_progress:
				raise
			exit = 'fail'
			new = None
		except:
			if not self.op_in_progress:
				raise
			traceback.print_exc()
			exit = 'fail'
			new = None

		if self.op_in_progress:
			op = self.op_in_progress
			self.set_oip(None)
			self.set_exec((op, exit))
		if new:
			self.move_to(new)
	
	def breakpoint(self):
		"""Return the type of breakpoint at exec-point.
		None - no breakpoint
		False - non-recording
		True - recording"""
		bt = self.breakpoints.get(self.exec_point, None)
		if bt is None:
			op = self.exec_point[0]
			if op.parent.start == op and op.next == None:
				return True		# Empty program
		else:
			if bt is True:
				del self.breakpoints[self.exec_point]
		return bt
	
	def do_one_step(self):
		"Execute the next op after exec_point, then:"
		"- position the point on one of the exits return."
		"- if there is no op to perform, call callback_on_return() or raise Done."
		"- if the operation is started but not complete, raise InProgress and "
		"  arrange to resume() later."
		if self.op_in_progress:
			alert("Already executing something.")
			raise Done()
		if not self.exec_point:
			alert("No current playback point.")
			raise Done()
		(op, exit) = self.exec_point

		bp = self.breakpoint()
		if bp == True or (self.single_step == 0 and bp is not None):
			print "Hit a breakpoint! At " + time.ctime(time.time())
			print "Foreach stack:", self.foreach_stack
			if bp:
				self.set_rec(self.exec_point)
			else:
				self.set_rec(None)
			self.single_step = 1
			for l in self.lists:
				l.show_prog(op.get_program())
			return
		
		next = getattr(op, exit)
		try:
			if next:
				self.set_oip(next)
				self.do_action(next.action)	# May raise InProgress
				return

			if exit == 'fail' and not self.innermost_failure:
				#print "Setting innermost_failure on", op
				self.innermost_failure = op

			if exit == 'fail' and not op.propagate_fail:
				self.single_step = 1
				for l in self.lists:
					l.show_prog(op.get_program())
				box = g.MessageDialog(None, 0, g.MESSAGE_QUESTION, g.BUTTONS_CANCEL,
						 'Operation failed. Do you want to record a failure case, '
						 'or allow errors to propagate to the parent block?')
				box.add_button('Propagate', 1)
				box.add_button('Record', 2)
				box.set_default_response(2)
				box.set_position(g.WIN_POS_CENTER)
				box.set_title(_('Operation failed'))
				resp = box.run()
				box.destroy()
				if resp == 1:
					op.set_propagate_fail(True)
					self.single_step = 0
					self.sched()
				elif resp == 2:
					self.set_rec((op, 'fail'))
					self.set_exec(None)
				return		# Stop

			# If we're in a block, try exiting from it...
			if isinstance(op.parent, Block):
				if self.start_block_iteration(op.parent, continuing = exit):
					return			# Looping...
				if not op.parent.is_toplevel():
					self.set_exec((op.parent, exit))
					return
		except Done:
			print "(skipped a whole program!)"
		if self.callback_on_return:
			cb = self.callback_on_return
			self.callback_on_return = None
			cb()
		else:
			raise Done()

	def set_oip(self, op):
		#print "set_oip:", self.exec_point
		if op:
			self.set_exec(None)
		self.op_in_progress = op
		for l in self.lists:
			l.update_points()

	def fast_global(self, name):
		"Search for nodes with this name anywhere under the root (//name)"
		#print "Fast global", name
		if ':' in name:
			(prefix, localName) = string.split(name, ':', 1)
		else:
			(prefix, localName) = (None, name)
		if self.current_nodes:
			src = self.current_nodes[-1]
		else:
			src = self.root
		namespaceURI = self.model.prefix_to_namespace(src, prefix)
		select = []
		def add(node):
			if node.nodeType != Node.ELEMENT_NODE:
				return
			if node.localName == localName and node.namespaceURI == namespaceURI:
				select.append(node)
			map(add, node.childNodes)
		add(self.root)
		self.move_to(select)

	# Actions...

	def do_global(self, pattern):
		if len(self.current_nodes) != 1:
			self.move_to(self.root)
		if pattern[:2] == '//':
			if fast_global.match(pattern):
				self.fast_global(pattern[2:])
				return
		
		assert not self.op_in_progress or (self.op_in_progress.action[1] == pattern)
		code = self.cached_code(pattern)

		ns = self.model.namespaces.uri
		c = Context.Context(self.get_current(), processorNss = ns)
		#print code
		nodes = code.evaluate(c)
		assert type(nodes) == list

		#don't select the document itself!
		#Also, don't select attributes (needed for XSLT stuff)
		nodes = [n for n in nodes if n.parentNode]
		
		#nodes = XPath.Evaluate(self.macro_pattern(pattern), contextNode = self.get_current())
		#print "Found", nodes
		self.move_to(nodes)
	
	def select_children(self):
		new = []
		for n in self.current_nodes:
			new.extend(n.childNodes)
		self.move_to(new)

	def select_region(self, path, ns = None):
		if len(self.current_nodes) == 0:
			raise Beep
		src = self.current_nodes[-1]

		ns = self.model.namespaces.uri
		c = Context.Context(src, [src], processorNss = ns)
		rt = XPath.Evaluate(path, context = c)
		node = None
		for x in rt:
			if not self.has_ancestor(x, self.root):
				print "[ skipping search result above root ]"
				continue
			if not node:
				node = x
		if not node:
			print "*** Search for '%s' in select_region failed" % path
			print "    (namespaces were '%s')" % ns
			raise Beep
		if node.parentNode != src.parentNode:
			print "Nodes must have same parent!"
			raise Beep
		on = 0
		selected = []
		for n in src.parentNode.childNodes:
			was_on = on
			if n is src or n is node:
				on = not was_on
			if on or was_on:
				selected.append(n)
		self.move_to(selected)
	
	def macro_pattern(self, pattern):
		"""Do the @CURRENT@ substitution for an XPath"""
		if len(self.current_nodes) != 1:
			return pattern
		node = self.get_current()
		if node.nodeType == Node.TEXT_NODE:
			current = node.data
		else:
			if self.current_attrib:
                                current = self.current_attrib.value
                        else:
                                current = node.nodeName
		pattern = pattern.replace('@CURRENT@', current)
		#print "Searching for", pattern
		return pattern
		
	def do_search(self, pattern, ns = None, toggle = FALSE):
		if len(self.current_nodes) == 0:
			src = self.root
		else:
			src = self.current_nodes[-1]
		
		# May be from a text_search...
		#assert not self.op_in_progress or (self.op_in_progress.action[1] == pattern)
		try:
			code = self.op_in_progress.cached_code
		except:
			from Ft.Xml.XPath import XPathParser
			code = XPathParser.new().parse(self.macro_pattern(pattern))
			if self.op_in_progress and pattern.find('@CURRENT@') == -1:
				self.op_in_progress.cached_code = code

		ns = self.model.namespaces.uri
		c = Context.Context(src, [src], processorNss = ns)
		
		rt = code.evaluate(c)
		node = None
		for x in rt:
			if not self.has_ancestor(x, self.root):
				print "[ skipping search result above root ]"
				continue
			if not node:
				node = x
			#if self.node_to_line[x] > self.current_line:
				#node = x
				#break
		if not node:
			#print "*** Search for '%s' failed" % pattern
			#print "    (namespaces were '%s')" % ns
			raise Beep
		if toggle:
			new = self.current_nodes[:]
			if node in new:
				new.remove(node)
			else:
				new.append(node)
			self.move_to(new)
		else:
			self.move_to(node)
	
	def do_text_search(self, pattern):
		pattern = self.macro_pattern(pattern)
		return self.do_search("//text()[ext:match('%s')]" % pattern)

	def subst(self, replace, replace_with):
		"re search and replace on the current node"
		nodes = self.current_nodes[:]
		check = len(nodes) == 1
		a = self.current_attrib
		if a:
			new, num = re.subn(replace, replace_with, a.value)
			if not num:
				raise Beep
			a = self.model.set_attrib(nodes[0], a.name, new)
			self.move_to(nodes[0], a)
		else:
			self.move_to([])
			final = []
			for n in nodes:
				if n.nodeType == Node.TEXT_NODE:
					old = n.data.replace('\n', ' ')
					new, num = re.subn(replace, replace_with, old)
					if check and not num:
						self.move_to(n)
						raise Beep
					self.model.set_data(n, new)
					final.append(n)
				elif n.nodeType == Node.ELEMENT_NODE:
					old = str(n.nodeName)
					new, num = re.subn(replace, replace_with, old)
					if check and not num:
						self.move_to(n)
						raise Beep
					new_ns, x = self.model.split_qname(n, new)
					final.append(self.model.set_name(n, new_ns, new))
				else:
					self.move_to(n)
					raise Beep
			self.move_to(final)

	def xpath(self, expr):
		"Put the result of 'expr' on the clipboard."
		expr = 'string(%s)' % expr
		if len(self.current_nodes) == 0:
			src = self.root
		else:
			src = self.current_nodes[-1]
		
		try:
			code = self.op_in_progress.cached_code
		except:
			from Ft.Xml.XPath import XPathParser
			code = XPathParser.new().parse(self.macro_pattern(expr))
			if self.op_in_progress and expr.find('@CURRENT@') == -1:
				self.op_in_progress.cached_code = code

		ns = self.model.namespaces.uri
		c = Context.Context(src, [src], processorNss = ns)
		
		rt = code.evaluate(c)

		self.clipboard = self.model.doc.createTextNode(rt)
		print "Result is", self.clipboard

	def split(self, sep):
		if self.get_current().nodeType == Node.TEXT_NODE:
			if sep:
				sep = sep.replace('\\n', '\n')
			else:
				sep = None
			result = self.get_current().data.split(sep)
			new = self.python_to_node(result)
			node = self.get_current()
			self.move_to([])
			self.model.replace_node(node, new)
			self.move_to(new)
		else:
			raise Beep

	def python(self, expr):
		"Replace node with result of expr(old_value)"
		if self.get_current().nodeType == Node.TEXT_NODE:
			vars = {'x': self.get_current().data, 're': re, 'sub': re.sub, 'string': string}
			result = eval(expr, vars)
			new = self.python_to_node(result)
			node = self.get_current()
			self.move_to([])
			self.model.replace_node(node, new)
			self.move_to(new)
		else:
			raise Beep

	def resume(self, exit = 'next'):
		"After raising InProgress, call this to start moving again."
		if self.op_in_progress:
			op = self.op_in_progress
			self.set_oip(None)
			self.set_exec((op, exit))
			if not self.single_step:
				self.sched()
				self.status_changed()
		else:
			print "(nothing to resume)"
		
	def ask(self, q):
		def ask_cb(result, self = self):
			if result is None:
				exit = 'fail'
			else:
				self.clipboard = self.model.doc.createTextNode(result)
				exit = 'next'
			self.resume(exit)
		from GetArg import GetArg
		box = GetArg('Input:', ask_cb, [q], destroy_return = 1)
		raise InProgress

	def python_to_node(self, data):
		"Convert a python data structure into a tree and return the root."
		if type(data) == list:
			nlist = self.model.doc.createElementNS(DOME_NS, 'dome:list')
			#nlist.setAttributeNS(XMLNS_NAMESPACE, 'xmlns:dome', DOME_NS)
			for x in data:
				li = self.model.doc.createElementNS(DOME_NS, 'dome:li')
				nlist.appendChild(li)
				li.appendChild(self.python_to_node(x))
			return nlist
		return self.model.doc.createTextNode(str(data))
	
	def _copy_attrib(self, n):
		a = self.model.doc.createAttributeNS(n.namespaceURI, n.nodeName)
		a.value = n.value
		return a
	
	def yank(self, deep = 1):
		if self.current_attrib:
			# XXX: don't need current_attrib anymore?
			a = self.current_attrib

			self.clipboard = self.model.doc.createElementNS(a.namespaceURI, a.nodeName)
			self.clipboard.appendChild(self.model.doc.createTextNode(a.value))
		else:
			self.clipboard = self.model.doc.createDocumentFragment()
			for n in self.current_nodes:
				if n.nodeType != Node.ATTRIBUTE_NODE:
					c = n.cloneNode(deep)
				else:
					c = self._copy_attrib(n)
				#print n, "->", c
				self.clipboard.appendChild(c)
		
		#print "Clip now", self.clipboard
	
	def shallow_yank(self):
		self.yank(0)
	
	def delete_shallow(self):
		nodes = self.current_nodes[:]
		if not nodes:
			return
		if self.root in nodes:
			raise Beep
		self.shallow_yank()
		self.move_to([])
		new = [x.parentNode for x in nodes]
		for n in nodes:
			self.model.delete_shallow(n)
		self.move_to(new)
	
	def delete_node_no_clipboard(self):
		self.delete_node(yank = 0)

	def delete_node(self, yank = 1):
		nodes = self.current_nodes[:]
		if not nodes:
			return
		if yank:
			self.yank()
		if self.current_attrib:
			ca = self.current_attrib
			self.current_attrib = None
			self.model.set_attrib(self.get_current(), ca.name, None)
			return
		if self.root in nodes:
			raise Beep
		self.move_to([])
		new = [x.parentNode for x in nodes]
		self.move_to(new)
		self.model.delete_nodes(nodes)
	
	def undo(self):
		nodes = self.current_nodes[:]
		self.move_to([])
		self.model.unlock(self.root)
		try:
			self.model.undo()
		finally:
			self.model.lock(self.root)
		self.move_to(filter(lambda x: self.has_ancestor(x, self.root), nodes))

	def redo(self):
		nodes = self.current_nodes[:]
		self.move_to([])
		self.model.unlock(self.root)
		try:
			self.model.redo()
		finally:
			self.model.lock(self.root)
		self.move_to(filter(lambda x: self.has_ancestor(x, self.root), nodes))
	
	def default_done(self, exit):
		"Called when execution of a program returns. op_in_progress has been "
		"restored - move to the exit."
		#print "default_done(%s)" % exit
		if self.op_in_progress:
			op = self.op_in_progress
			self.set_oip(None)
			self.set_exec((op, exit))
		else:
			print "No operation to return to!"
			c = self.call_on_done
			if c:
				self.call_on_done = None
				c(exit)
			elif exit == 'fail':
				self.jump_to_innermost_failure()
			raise Done()
	
	def jump_to_innermost_failure(self):
		assert self.innermost_failure != None

		print "Returning to innermost failure:", self.innermost_failure
		self.set_exec((self.innermost_failure, 'fail'))
		for l in self.lists:
			if hasattr(l, 'set_innermost_failure'):
				l.set_innermost_failure(self.innermost_failure)

	def play(self, name, done = None):
		"Play this macro. When it returns, restore the current op_in_progress (if any)"
		"and call done(exit). Default for done() moves exec_point."
		"done() is called from do_one_step() - usual rules apply."

		prog = self.name_to_prog(name)
		self.innermost_failure = None

		if not done:
			done = self.default_done

		def cbor(self = self, op = self.op_in_progress, done = done,
				name = name,
				old_cbor = self.callback_on_return,
				old_ss = self.single_step):
			"We're in do_one_step..."

			#print "Return from '%s'..." % name

			if old_ss == 2 and self.single_step == 0:
				self.single_step = old_ss
			self.callback_on_return = old_cbor

			o, exit = self.exec_point
			if op:
				#print "Resume op '%s' (%s)" % (op.program.name, op)
				self.pop_stack()
				self.set_oip(op)
			return done(exit)

		self.callback_on_return = cbor

		if self.single_step == 2:
			self.single_step = 0
			
		if self.op_in_progress:
			self.push_stack(self.op_in_progress)
			self.set_oip(None)
		self.play_block(prog.code)
		self.sched()
		self.status_changed()
		raise InProgress
	
	def start_block_iteration(self, block, continuing = None):
		"True if we are going to run the block, False to exit the loop"
		"Continuing is 'next' or 'fail' if we reached the end of the block."
		#print "Start interation"
		if not self.foreach_stack:
			raise Done
		stack_block, nodes_list, restore, old_mark = self.foreach_stack[-1]
		if stack_block != block:
			self.reset_foreach_stack()
			self.update_stack()
			raise Exception("Reached the end of a block we never entered")

		if continuing:
			if block.enter:
				self.leave()
			if block.foreach:
				restore.extend(self.current_nodes)
			if continuing == 'fail':
				print "Error in block; exiting early in program", block.get_program()
				if old_mark:
					[self.model.unlock(x) for x in old_mark]
				self.foreach_stack.pop()
				self.update_stack()
				return 0
			while nodes_list and nodes_list[0].parentNode == None:
				print "Skipping deleted node", nodes_list[0]
				del nodes_list[0]

		if not nodes_list:
			self.foreach_stack.pop()
			self.update_stack()
			if block.foreach:
				nodes = filter(lambda x: self.has_ancestor(x, self.root), restore)
				self.move_to(nodes)
			if old_mark is not None:
				self.set_marked(old_mark)
				[self.model.unlock(x) for x in old_mark]
			return 0	# Nothing left to do
		nodes = nodes_list[0]
		del nodes_list[0]
		self.move_to(nodes)

		if nodes_list:
			print "[ %d after this ]" % len(nodes_list),
			sys.stdout.flush()

		if block.enter:
			self.enter()
		self.set_exec((block.start, 'next'))
		return 1
	
	def play_block(self, block):
		assert isinstance(block, Block)
		#print "Enter Block!"
		if block.foreach:
			list = self.current_nodes[:]
		else:
			list = [self.current_nodes[:]]	# List of one item, containing everything
			
		if block.restore:
			marks = self.marked.copy()
			[self.model.lock(x) for x in marks]
		else:
			marks = None
		self.foreach_stack.append((block, list, [], marks))
			
		self.update_stack()
		if not self.start_block_iteration(block):
			# No nodes selected...
			if not block.is_toplevel():
				self.set_exec((block, 'next'))
			else:
				self.set_oip(None)
				self.set_exec((block.start, 'next'))
				raise Done
	
	def Block(self):
		assert self.op_in_progress
		oip = self.op_in_progress
		self.set_oip(None)
		self.play_block(oip)
		if not self.single_step:
			self.sched()
			raise InProgress
	
	def sched(self):
		if self.op_in_progress:
			raise Exception("Operation in progress")
		if self.idle_cb:
			raise Exception("Already playing!")
		self.idle_cb = self.idle_add(self.play_callback)

	def play_callback(self):
		self.idle_remove(self.idle_cb)
		self.idle_cb = 0
		try:
			self.in_callback = 1
			try:
				self.do_one_step()
			finally:
				self.in_callback = 0
		except Done:
			(op, exit) = self.exec_point
			if exit == 'fail' and self.innermost_failure:
				self.jump_to_innermost_failure()
			print "Done, at " + time.ctime(time.time())
			self.run_new()
			return 0
		except InProgress:
			# print "InProgress"
			return 0
		except:
			type, val, tb = sys.exc_info()
			list = traceback.extract_tb(tb)
			stack = traceback.format_list(list[-2:])
			ex = traceback.format_exception_only(type, val) + ['\n\n'] + stack
			traceback.print_exception(type, val, tb)
			print "Error in do_one_step(): stopping playback"
			node = self.op_in_progress
			self.set_oip(None)
			if node:
				self.set_exec((node, 'fail'))
			self.status_changed()
			return 0
		if self.op_in_progress or self.single_step:
			self.status_changed()
			return 0
		self.sched()
		return 0
	
	def set_status(self, message = None):
		"Set the status bar message."
		for d in self.displays:
			if hasattr(d, 'set_status'): d.set_status(message)

	def status_changed(self):
		for display in self.displays:
			if hasattr(display, 'update_state'):
				display.update_state()

	def map(self, name):
		print "Map", name

		nodes = self.current_nodes[:]
		if not nodes:
			print "map of nothing: skipping..."
			return
		inp = [nodes, None]	# Nodes, next
		def next(exit = exit, self = self, name = name, inp = inp):
			"This is called while in do_one_step() - normal rules apply."
			nodes, next = inp
			print "[ %d to go ]" % len(nodes),
			sys.stdout.flush()
			if exit == 'fail':
				print "Map: nodes remaining, but an error occurred..."
				return self.default_done(exit)
			while nodes and nodes[0].parentNode == None:
				print "Skipping deleted node", nodes[0]
				del nodes[0]
			if not nodes:
				return self.default_done(exit)
			self.move_to(nodes[0])
			del nodes[0]
			if not nodes:
				next = None
			#print "Map: calling play (%d after this)" % len(nodes)
			self.play(name, done = next)	# Should raise InProgress
		if nodes is self.current_nodes:
			raise Exception("Slice failed!")
		inp[1] = next
		next('next')
	
	def name_to_prog(self, name):
		comps = string.split(name, '/')
		prog = self.model.root_program
		if prog.name != comps[0]:
			raise Exception("No such program as '%s'!" % name)
		del comps[0]
		while comps:
			prog = prog.subprograms[comps[0]]
			del comps[0]
		return prog

	def change_node(self, new_data):
		nodes = self.current_nodes
		if not nodes:
			return
		self.move_to([])
		if nodes[0].nodeType == Node.ELEMENT_NODE:
			# Slow, so do this here, even if vaguely incorrect...
			assert ' ' not in new_data
			if ':' in new_data:
				(prefix, localName) = string.split(new_data, ':', 1)
			else:
				(prefix, localName) = (None, new_data)
			namespaceURI = self.model.prefix_to_namespace(nodes[0], prefix)
			out = []
			for node in nodes:
				if node is self.root:
					self.model.unlock(self.root)
					new = self.model.set_name(node, namespaceURI, new_data)
					self.model.lock(new)
					self.root = new
				else:
					new = self.model.set_name(node, namespaceURI, new_data)
				out.append(new)
			self.move_to(out)
		else:
			for node in nodes:
				self.model.set_data(node, new_data)
			self.move_to(nodes)

	def add_node(self, where, data):
		cur = self.get_current()
		if where[1] == 'e':
			if ':' in data:
				(prefix, localName) = string.split(data, ':', 1)
			else:
				(prefix, localName) = (None, data)
			namespaceURI = self.model.prefix_to_namespace(self.get_current(), prefix)
			new = self.model.doc.createElementNS(namespaceURI, data)
		elif where[1] == 'a':
			self.add_attrib(None, data)
			return
		else:
			new = self.model.doc.createTextNode(data)
		
		try:
			if where[0] == 'i':
				self.model.insert_before(cur, new)
			elif where[0] == 'a':
				self.model.insert_after(cur, new)
			elif where[0] == 'e':
				self.model.insert_before(None, new, parent = cur)
			else:
				self.model.insert(cur, new)
		except:
			raise Beep

		self.move_to(new)

	def request_from_node(self, node, attrib):
		"""Return a urllib2.Request object. If attrib is set then the URI is
		taken from that, otherwise search for a good attribute."""
		uri = None
		if node.nodeType == Node.TEXT_NODE:
			uri = node.nodeValue
		else:
			if attrib:
				uri = attrib.value
			elif node.hasAttributeNS(None, 'uri'):
				uri = node.getAttributeNS(None, 'uri')
			else:
				for attr in node.attributes.keys():
					a_node = node.attributes[attr]
					if a_node.namespaceURI == XMLNS_NAMESPACE:
						continue
					uri = a_node.value
					if uri.find('//') != -1 or uri.find('.htm') != -1:
						break
		if not uri:
			print "Can't suck", node, "(no uri attribute found)"
			raise Beep
		if uri.find('//') == -1:
			base = self.model.get_base_uri(node)
			if not ':' in base:
				base = os.path.dirname(base)
			print "Relative URI..."
			if base:
				print "Base URI is:", base, "add", uri
				if uri.startswith('/'):
					uri = urlparse.urljoin(base, uri)
				else:
					uri = base + '/' + uri
				print "Final URI is:", uri
			else:
				pass
				#print "Warning: Can't find 'uri' attribute!"
		if not ':' in uri:
			uri = 'file://' + uri
		request = urllib2.Request(uri)

		return request
	
	def http_post(self):
		node = self.get_current()
		attrs = node.attributes
		post = []
		request = self.request_from_node(node, self.current_attrib)
		for (ns,name) in attrs.keys():
			if ns is not None: continue
			value = str(attrs[(ns, name)].value)
			if name.startswith('header-'):
				request.add_header(str(name)[7:], value)
			else:
				post.append((str(name), value))
				
		request.add_data(urllib.urlencode(post))
		node = self.suck_node(node, request)
		if node:
			self.move_to(node)
	
	def suck(self, md5_only = 0):
		nodes = self.current_nodes[:]
		attrib = self.current_attrib
		self.move_to([])
		final = []
		for x in nodes:
			request = self.request_from_node(x, attrib)
			try:
				new = self.suck_node(x, request, md5_only = md5_only)
				if not new:
					raise Beep
				final.append(new)
			finally:
				self.move_to(x)
		self.move_to(final)
	
	def suck_md5(self):
		self.suck(md5_only = 1)
		
	def suck_node(self, node, request, md5_only = 0):
		"""Load the resource specified by request and replace 'node' with the
		sucked data."""
		uri = request.get_full_url()
		self.set_status("Fetching %s (connecting)..." % uri)
		new = None
		try:
			if uri.startswith('file:///'):
				assert not request.has_data()
				stream = open(uri[7:])
			else:
				if request.has_data(): print "POSTING", request.get_data()
				stream = urllib2.urlopen(request)
				headers = stream.info().headers
			
			self.set_status("Fetching %s (downloading)..." % uri)
			data = stream.read()
			self.set_status("Fetching %s (parsing)..." % uri)

			if not data.startswith('<?xml'): data = support.to_html_doc(data)
			
			try:
				root = support.parse_data(data, uri)
			except:
				raise Beep
			
			new = self.model.import_with_ns(root.documentElement)
			new.setAttributeNS(None, 'uri', uri)

			self.move_to([])
			if node == self.root:
				self.model.unlock(self.root)
				self.model.replace_node(self.root, new)
				self.model.strip_space(new)
				self.model.lock(new)
				self.root = new
			else:
				self.model.replace_node(node, new)
				self.model.strip_space(new)

		finally:
			self.set_status()
			return new
	
	def put_before(self):
		node = self.get_current()
		if self.clipboard == None:
			raise Beep
		new = self.clipboard.cloneNode(1)
		try:
			self.model.insert_before(node, new)
		except:
			raise Beep

	def put_after(self):
		node = self.get_current()
		if self.clipboard == None:
			raise Beep
		new = self.clipboard.cloneNode(1)
		self.model.insert_after(node, new)
	
	def put_replace(self):
		node = self.get_current()
		if self.clipboard == None:
			print "No clipboard!"
			raise Beep
		if self.current_attrib:
			if self.clipboard.nodeType == Node.DOCUMENT_FRAGMENT_NODE:
				value = self.clipboard.childNodes[0].data
			else:
				value = self.clipboard.data
			a = self.current_attrib
			value = value.replace('\n', ' ')
			a = self.model.set_attrib(node, a.name, value)
			self.move_to(node, a)
			return
		if self.clipboard.nodeType == Node.DOCUMENT_FRAGMENT_NODE:
			if len(self.clipboard.childNodes) != 1:
				print "Multiple nodes in clipboard!"
				raise Beep
			new = self.clipboard.childNodes[0].cloneNode(1)
		else:
			new = self.clipboard.cloneNode(1)
		if new.nodeType != Node.ELEMENT_NODE:
			raise Beep
		self.move_to([])
		try:
			if node == self.root:
				self.model.unlock(self.root)
				try:
					self.model.replace_node(self.root, new)
					self.root = new
				finally:
					self.model.lock(self.root)
			else:
				self.model.replace_node(node, new)
			self.move_to(new)
		except:
			type, val, tb = sys.exc_info()
			traceback.print_exception(type, val, tb)
			print "Replace failed!"
			raise Beep
	
	def put_as_child_end(self):
		self.put_as_child(end = 1)

	def put_as_child(self, end = 0):
		clip = self.clipboard
		node = self.get_current()
		if node.nodeType == Node.ATTRIBUTE_NODE:
			node = node.parentNode
		if clip is None:
			raise Beep

		if clip.nodeType == Node.DOCUMENT_FRAGMENT_NODE:
			clip = clip.childNodes
		else:
			clip = [clip]

		attribs = []
		new = []
		for src in clip:
			if src.nodeType == Node.ATTRIBUTE_NODE:
				attribs.append((src.name, src.value))
			else:
				new.append(src.cloneNode(1))
		try:
			if end:
				self.model.insert_before(None, new, parent = node)
			else:
				self.model.insert(node, new, index = 0)
		except:
			raise Beep

		for a in attribs:
			new.append(self.model.set_attrib(node, *a))
		self.move_to(new)
	
	def yank_value(self):
		if not self.current_attrib:
			raise Beep
		value = self.current_attrib.value
		self.clipboard = self.model.doc.createTextNode(value)
		#print "Clip now", self.clipboard
	
	def yank_attribs(self, name = None):
		if name:
			print "yank_attribs: DEPRECATED -- use Yank instead!"
		self.clipboard = self.model.doc.createDocumentFragment()
		if name:
			if not self.get_current().hasAttributeNS(None, name):
				raise Beep
			attribs = [self.get_current().getAttributeNodeNS(None, name)]
		else:
			attribs = []
			dict = self.get_current().attributes
			for a in dict.keys():
				attribs.append(dict[a])

		# Make sure the attributes always come out in the same order
		# (helps with macros).
		def by_name(a, b):
			diff = cmp(a.name, b.name)
			if diff == 0:
				diff = cmp(a.namespaceURI, b.namespaceURI)
			return diff
			
		attribs.sort(by_name)
		for a in attribs:
			n = self.model.doc.createElementNS(a.namespaceURI, a.nodeName)
			n.appendChild(self.model.doc.createTextNode(a.value))
			self.clipboard.appendChild(n)
		#print "Clip now", self.clipboard
	
	def paste_attribs(self):
		if self.clipboard.nodeType == Node.DOCUMENT_FRAGMENT_NODE:
			attribs = self.clipboard.childNodes
		else:
			attribs = [self.clipboard]
		new = []
		for a in attribs:
			try:
				new.append((a.nodeName, a.childNodes[0].data))
			except:
				raise Beep
		for node in self.current_nodes:
			# XXX: Set NS attribs first...
			for (name, value) in new:
				self.model.set_attrib(node, name, value)
	
	def compare(self):
		"Ensure that all selected nodes have the same value."
		if len(self.current_nodes) < 2:
			raise Beep		# Not enough nodes!
		base = self.current_nodes[0]
		for n in self.current_nodes[1:]:
			if not same(base, n):
				raise Beep(may_record = 1)
	
	def fail(self):
		raise Beep(may_record = 1)
	
	def do_pass(self):
		pass
	
	def assert_xpath(self, xpath):
		"""Evaluate xpath as a boolean, and fail if false."""
		src = self.get_current()
		ns = self.model.namespaces.uri
		c = Context.Context(src, [src], processorNss = ns)
		
		rt = XPath.Evaluate(xpath, context = c)
		#print "Got", rt
		if not rt:
			raise Beep(may_record = 1)
	
	def xslt_fail_if(self, xpath):
		"""Evaluate xpath as a boolean, and fail if true."""
		src = self.get_current()
		ns = self.model.namespaces.uri
		c = Context.Context(src.parentNode, [src.parentNode], processorNss = ns)
		
		rt = XPath.Evaluate(xpath, context = c)
		#print "Got", rt
		if src in rt:
			raise Beep(may_record = 1)
	
	def attribute(self, namespace = None, attrib = ''):
		node = self.get_current()

		if attrib == '':
			self.move_to(node)
			return

		if attrib == 'xmlns':
			attrib = None
		#print "(ns, attrib)", `namespace`, attrib

		a = node.attributes.get((namespace, attrib), None)

		if a:
			self.move_to(node, a)
		else:
			print "No such attribute"
			print "Looking for %s in %s" % ((namespace, attrib), node.attributes)
			raise Beep
	
	def set_attrib(self, value):
		a = self.current_attrib
		if not a:
			raise Beep
		node = self.get_current()
		a = self.model.set_attrib(node, a.name, value)
		self.move_to(node, a)
	
	def rename_attrib(self, new):
		a = self.current_attrib
		if not a:
			raise Beep
		node = self.get_current()
		new_attr = self.model.set_attrib(node, new, a.value)
		self.model.set_attrib(node, a.name, None)
		self.move_to(node, new_attr)
	
	def add_attrib(self, UNUSED, name, value = ''):
		node = self.get_current()
		a = self.model.set_attrib(node, name, value)
		self.move_to(node, a)
	
	def set_root_from_doc(self, doc):
		new = self.model.import_with_ns(doc.documentElement)

		if self.root:
			self.model.unlock(self.root)
		self.move_to([])
		self.model.replace_node(self.root, new)
		self.model.lock(new)
		self.root = new
		self.move_to(self.root)

	def load_html(self, path):
		"Replace root with contents of this HTML file."
		print "Reading HTML..."
		doc = self.model.load_html(path)
		self.set_root_from_doc(doc)
		
	def load_xml(self, path):
		"Replace root with contents of this XML (or Dome) file."
		print "Reading XML..."
		data = file(path).read()
		doc = support.parse_data(data, path)
		self.set_root_from_doc(doc)

	def select_dups(self):
		node = self.get_current()
		select = []
		for n in node.parentNode.childNodes:
			if n is node:
				continue
			if same(node, n):
				select.append(n)
		self.move_to(select)
	
	def select_marked_region(self, attr = "unused"):
		select = []
		if len(self.marked) != 1:
			raise Beep("Must be exactly one marked node!")
		if len(self.current_nodes) != 1:
			raise Beep("Must be exactly one selected node!")
		import Path
		a = Path.path_to(self.get_current())
		b = Path.path_to(self.marked.keys()[0])

		while a and b and a[0] == b[0]:
			del a[0]
			del b[0]

		if a and b:
			select = []
			s = 0
			a = a[0]
			b = b[0]
			for x in a.parentNode.childNodes:
				if x == a:
					s = not s
				elif x == b:
					s = not s
				if s:
					select.append(x)
			self.move_to(select)
		else:
			raise Beep("One node is a parent of the other!")
	
	def show_html(self):
		from HTML import HTML
		HTML(self.model, self.get_current()).show()
	
	def show_canvas(self):
		from Canvas import Canvas
		Canvas(self, self.get_current()).show()
	
	def toggle_hidden(self, message = None):
		"""'message' is a XPath to calculate the message to display.
		If None, nodes are toggled between hidden and not hidden."""
		if message:
			from Ft.Xml.XPath import XPathParser
			code = XPathParser.new().parse('string(%s)' % message)
		else:
			code = None

		nodes = self.current_nodes[:]
		self.move_to([])
		hidden = self.model.hidden
		hidden_code = self.model.hidden_code
		for node in nodes:
			if node.nodeType != Node.ELEMENT_NODE:
				raise Beep
			if code:
				hidden_code[node] = code
			if node in hidden:
				del hidden[node]
			else:
				if node in hidden_code:
					ns = self.model.namespaces.uri
					c = Context.Context(node, [node], processorNss = ns)
					hidden[node] = hidden_code[node].evaluate(c).strip()
				else:
					hidden[node] = 'hidden'
		self.model.update_all(self.root)
		self.move_to(nodes)
	
	def soap_send(self):
		copy = node_to_xml(self.get_current())
		env = copy.documentElement
		from Ft.Xml.Lib.Nss import GetAllNs
		nss = GetAllNs(env)
		for p, u in self.model.namespaces.uri.iteritems():
			if p in nss:
				assert nss[p] == u
			elif p not in ('xml', 'xmlns'):
				env.setAttributeNS(XMLNS_NAMESPACE, 'xmlns:%s' % p, u)

		if env.namespaceURI != SOAPENV_NS:
			alert("Not a SOAP-ENV:Envelope (bad namespace)")
			raise Done()
		if env.localName != 'Envelope':
			alert("Not a SOAP-ENV:Envelope (bad local name)")
			raise Done()

		if len(env.childNodes) != 2:
			alert("SOAP-ENV:Envelope must have one header and one body")
			raise Done()

		kids = elements(env)
		head = kids[0]
		body = kids[1]

		if head.namespaceURI != SOAPENV_NS or \
		   head.localName != 'Head':
			alert("First child must be a SOAP-ENV:Head element")
			raise Done()

		if body.namespaceURI != SOAPENV_NS or \
		   body.localName != 'Body':
			alert("Second child must be a SOAP-ENV:Body element")
			raise Done()

		sft = None
		for header in elements(head):
			if header.namespaceURI == DOME_NS and header.localName == 'soap-forward-to':
				sft = header
				break
			print header.namespaceURI
			print header.localName

		if not sft:
			alert("Head must contain a dome:soap-forward-to element")
			raise Done()

		dest = sft.childNodes[0].data
		parent = sft.parentNode
		if len(elements(parent)) == 1:
			sft = parent
			parent = sft.parentNode	# Delete the whole header
		parent.removeChild(sft)

		import httplib, urlparse

		(scheme, addr, path, p, q, f) = urlparse.urlparse(dest, allow_fragments = 0)
		if scheme != 'http':
			alert("SOAP is only supported for 'http:' -- sorry!")
			raise Done()

		stream = StrGrab()
		PrettyPrint(copy, stream = stream)
		message = stream.data
		print message

		conn = httplib.HTTP(addr)
		conn.putrequest("POST", path)
		conn.putheader('Content-Type', 'text/xml; charset="utf-8"')
		conn.putheader('Content-Length', str(len(message)))
		conn.putheader('SOAPAction', '')
		conn.endheaders()
		conn.send(message)
		(code, r_mess, r_headers) = conn.getreply()

		reply = conn.getfile().read()
		print "Got:\n", reply

		new_doc = support.parse_data(reply, dest)

		#new = self.model.doc.importNode(new_doc.documentElement, 1)
		new = self.model.import_with_ns(new_doc.documentElement)
		
		self.model.strip_space(new)

		old = self.get_current()
		self.move_to([])
		self.model.replace_node(old, new)
		self.move_to(new)
	
	def program_changed(self, changed_op):
		print "Check points..."
		if self.rec_point:
			(op, exit) = self.rec_point
			if not op.parent:
				print "Lost rec_point"
				self.rec_point = None
		if self.exec_point:
			(op, exit) = self.exec_point
			if not op.parent:
				print "Lost exec_point"
				self.exec_point = None
		for l in self.lists:
			l.update_points()
		self.status_changed()
		
	def prog_tree_changed(self):
		pass
	
	def export_all(self):
		doc = implementation.createDocument(DOME_NS, 'dome:dome', None)
		
		doc.documentElement.appendChild(self.model.namespaces.to_xml(doc))
		
		node = self.model.root_program.to_xml(doc)
		doc.documentElement.appendChild(node)
		node = doc.createElementNS(DOME_NS, 'dome:dome-data')
		doc.documentElement.appendChild(node)

		if self.chroots:
			print "*** WARNING: Saving from a chroot!"
		model = self.model
		data = doc.importNode(model.doc.documentElement, 1)
		node.appendChild(data)

		return doc
	
	def blank_all(self):
		doc = implementation.createDocument(None, 'root', None)
		self.move_home()
		self.clipboard = self.model.doc.createElementNS(None, 'root')
		self.put_replace()
	
	def mark_switch(self):
		new = self.marked.keys()
		self.set_marked(self.current_nodes)
		self.move_to(new)
	
	def set_marked(self, new):
		update = self.marked
		for x in self.marked.keys():
			self.model.unlock(x)
		self.marked = {}
		for x in new:
			self.model.lock(x)
			self.marked[x] = None
			update[x] = None
		update = update.keys()
		for display in self.displays:
			display.marked_changed(update)

	def mark_selection(self):
		self.set_marked(self.current_nodes)
	
	def cached_code(self, expr):
		"""If self.op_in_progress has cached code, return that. Otherwise, compile and cache code."""
		try:
			return self.op_in_progress.cached_code
		except:
			from Ft.Xml.XPath import XPathParser
			code = XPathParser.new().parse(self.macro_pattern(expr))
		if self.op_in_progress and expr.find('@CURRENT@') == -1:
			self.op_in_progress.cached_code = code
		return code
	
	def move_selection(self, xpath):
		"Move selected node to parent identified by xpath."
		src = self.get_current()
		code = self.cached_code(xpath)

		c = Context.Context(src, [src], processorNss = self.model.namespaces.uri)
		rt = code.evaluate(c)

		if not rt:
			raise Beep
		if len(rt) != 1:
			print "Multiple matches!", rt
			raise Beep

		dst, = rt
		self.move_to(rt)
		self.model.delete_nodes([src])
		self.model.insert(dst, src)
	
	def move_marked(self):
		to = self.get_current()
		if to.nodeType != Node.ELEMENT_NODE:
			raise Beep
		tmp = self.marked
		self.clear_mark()
		self.model.delete_nodes(tmp)
		for n in tmp:
			self.model.insert(to, n)
		self.set_marked(tmp)
	
	def clear_mark(self):
		self.set_marked([])
	
	def normalise(self):
		self.model.normalise(self.get_current())
	
	def remove_ns(self):
		print "remove_ns: Disabled"
		return
		nodes = self.current_nodes[:]
		self.move_to([])
		nodes = map(self.model.remove_ns, nodes)
		self.move_to(nodes)
	
	def convert_to(self, fn):
		nodes = self.current_nodes[:]
		self.move_to([])
		nodes = map(fn, nodes)
		self.move_to(nodes)
	def convert_to_element(self): self.convert_to(self.model.convert_to_element)
	def convert_to_text(self): self.convert_to(self.model.convert_to_text)
	def convert_to_comment(self): self.convert_to(self.model.convert_to_comment)

class StrGrab:
	data = ''

	def write(self, str):
		self.data += str
