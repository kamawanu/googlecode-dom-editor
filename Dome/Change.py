# All changes go through here so we can undo...

# Which undo list operations will affect.
# Normal ops add to the undo list.
# Undo ops add to the redo list.
# Redo ops add to the undo list, but don't clear the redo list.
undo_state = '__dom_undo'
undo_clear = 1

# These two are just for convenience...
def insert(node, new, index = 0):
	if len(node.childNodes) > index:
		insert_before(node.childNodes[index], new)
	else:
		insert_before(None, new, parent = node)

def insert_after(node, new):
	insert_before(node.nextSibling, new, parent = node.parentNode)

# These actually modifiy the DOM
def set_name(node, new):
	# XXX: Hack!
	add_undo(node, lambda node = node, old = node.nodeName:
			set_name(node, old))
	node.__dict__['__nodeName'] = new

def insert_before(node, new, parent = None):
	"Insert 'new' before 'node'. If 'node' is None then insert at the end"
	"of parent's children."
	if not parent:
		parent = node.parentNode
	parent.insertBefore(new, node)
	add_undo(parent, lambda new = new: delete(new))
	
def delete(node):
	next = node.nextSibling
	parent = node.parentNode
	parent.removeChild(node)
	add_undo(parent,
		lambda parent = parent, next = next, node = node:
				insert_before(next, node, parent = parent))

def replace_node(old, new):
	old.parentNode.replaceChild(new, old)
	add_undo(new.parentNode,
		lambda old = old, new = new:
			replace_node(new, old))

def set_data(node, new):
	old = node.data
	node.data = new
	add_undo(node,
		lambda node = node, old = old:
			set_data(node, old))

# Support

op = 0

def newest_change(node, history):
	"Return the most recent (node,time) change to the 'history' list."

	try:
		best_node, best_time = node, getattr(node, history)[-1]
	except:
		best_node, best_time = None, 0
	
	for k in node.childNodes:
		n, t = newest_change(k, history)
		if n and (best_node == None or t > best_time):
			best_node, best_time = n, t
	return best_node, best_time

def can_undo(node):
	n, t = newest_change(node, '__dom_undo')
	return n != None

def can_redo(node):
	n, t = newest_change(node, '__dom_redo')
	return n != None

# Undo and redo stuff
def add_undo(node, fn):
	"Attempting to undo changes to 'node' will call this fn."
	global op
	op += 1
	
	if not hasattr(node, undo_state):
		setattr(node, undo_state, [])
	getattr(node, undo_state).append((op, fn))
	if undo_clear and hasattr(node, '__dom_redo'):
		del node.__dom_redo

def do_undo(node):
	"Undo changes to this node (including descendants)."
	if not can_undo(node):
		return
	
	node, time = newest_change(node, '__dom_undo')
	
	op, fn = node.__dom_undo.pop()

	global undo_state, undo_clear
	undo_state = '__dom_redo'
	undo_clear = 0
	fn()
	undo_clear = 1
	undo_state = '__dom_undo'

def do_redo(node):
	"Redo undos on this node (including descendants)."
	if not can_redo(node):
		return
	
	node, time = newest_change(node, '__dom_redo')
	
	op, fn = node.__dom_redo.pop()

	global undo_state, undo_clear
	undo_clear = 0
	fn()
	undo_clear = 1