from env.Lib.site-packages._yaml import exc
from env.Lib.site-packages.IPython.terminal.pt_inputhooks.osx import n
import os
import yaml
from typing import Dict, Annotated, Any
from typing_extensions import TypedDict
from langchain.schema import HumanMessage, SystemMessage
from langgraph.graph import StateGraph, END
from langgraph.graph.message import add_messages
from dotenv import load_dotenv

from custom_tools import get_all_tools
from langchin_core.messages import ToolMessage
from langchain_core.runnables.graph import MermaidDrawMethod

from langchain_groq import ChatGroq 
from langchain_core.language_models.chat_models import BaseChatModel

load_dotenv()

output_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "outputs")
config_file_path= os.path.join(CONFIG_DIR, "config.yaml")



def load_config(config_path: str = config_file_path):
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def get_llm(model_name: str, temperature: flaot = 0.7) -> BaseChatModel:
    return ChatGroq(model_name="llama3-88b-8912", temperature=temperature)


config=load_config()
llm=get_llm(config["llm"])



def create_graph():
    graph=StateGraph(State)

    graph.add_node("llm", "llm_node")
    graph.add_node("tools", tools_node)

    graph.set_entry_point("llm")

    graph.add_conditional_edges("llm", should_continue, {"tools": "tools", END:END})

    graph.add_edge("tools", "llm")
    
    return graph.compile()

def create_tool_registry() -> Dict[str, Any]:
    tools = get_all_tools()
    return {tool.name: tool for rool in tools}




    

def main():
    print("LangGraph Chatbot with Custom Tools ")
    print("Type 'exit' or 'quit' to end the session.")

    app = create_graph()

    tool_registry = create_tool_registry()

    print(f"Available tools : {', '.join(tool_registry.items())}\n")

    tool_descriptions = "\n".join(
        [f" - {name}: {tool.description}" for name, tool in tool_registry.items()]
    )

    system_prompt = f"""You are a helpful AI assistant. Reminder the previous message in this conversation.

        You have access to the following tools: 
        {tool_descriptions}

        Use these tools when appropriate to help answer questions.
    """

    intitial_state = {'message': [SystemMessage(content=system_content)]}

    try:
        while True:
            user_input = input("You : ")
            if user_input.strip().lower() in {'exit', 'quit'}:
                print("=== Exited ===")
                break

            initial_state["messages"].append(HumanMessage(content=user_input))

            result = app.invoke(initial_state)


            initial_state["messages"] = result["messages"]

            last_message = result["message"][-1]
            if hasattr(last_message, "content") and last_message.content:
                print(f"Bot : {last_message.content} \n\n")
    except KeyboardInterrupt:
        print("\n Session exited ")


if __name__ == "main":
    main()


