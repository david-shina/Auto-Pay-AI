from langgraph.graph import StateGraph, END
from .state import AgentState
from .nodes import make_decision_node

def build_graph():
    workflow = StateGraph(AgentState)

    # Add our nodes
    workflow.add_node("reasoning", make_decision_node)

    # Set the entry point
    workflow.set_entry_point("reasoning")

    # For now, we go straight to the end. 
    # Later, we will add a 'payment' node here.
    workflow.add_edge("reasoning", END)

    return workflow.compile()
