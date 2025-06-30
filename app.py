import os
import chainlit as cl
from azure.ai.projects import AIProjectClient
from azure.identity import ManagedIdentityCredential, DefaultAzureCredential
import asyncio
from typing import Optional

# Azure AI Foundry configuration
PROJECT_CONNECTION_STRING = os.getenv("AZURE_AI_PROJECT_CONNECTION_STRING")
AGENT_ID = os.getenv("AZURE_AI_AGENT_ID")
# For system-assigned managed identity, these are typically not needed in the connection string
# but can be used for explicit client construction
AZURE_SUBSCRIPTION_ID = os.getenv("AZURE_SUBSCRIPTION_ID")
AZURE_RESOURCE_GROUP = os.getenv("AZURE_RESOURCE_GROUP")
AZURE_PROJECT_NAME = os.getenv("AZURE_PROJECT_NAME")

class AzureAIAgent:
    def __init__(self):
        self.client = None
        self.agent = None
        self.thread = None
        
    def initialize(self):
        """Initialize the Azure AI Project client and agent using system-assigned managed identity"""
        try:
            # Use system-assigned managed identity credential
            # This will automatically use the managed identity assigned to the Azure resource
            credential = ManagedIdentityCredential()
            
            # Fallback to DefaultAzureCredential for local development
            # DefaultAzureCredential will try multiple authentication methods including managed identity
            fallback_credential = DefaultAzureCredential()
            
            # Initialize the client
            if PROJECT_CONNECTION_STRING:
                # Use connection string with managed identity credential
                self.client = AIProjectClient.from_connection_string(
                    conn_str=PROJECT_CONNECTION_STRING,
                    credential=credential
                )
            elif all([AZURE_SUBSCRIPTION_ID, AZURE_RESOURCE_GROUP, AZURE_PROJECT_NAME]):
                # Alternative: construct client with individual parameters
                self.client = AIProjectClient(
                    credential=credential,
                    subscription_id=AZURE_SUBSCRIPTION_ID,
                    resource_group_name=AZURE_RESOURCE_GROUP,
                    project_name=AZURE_PROJECT_NAME
                )
            else:
                # Try with DefaultAzureCredential as fallback for local development
                print("Using DefaultAzureCredential fallback for local development")
                if PROJECT_CONNECTION_STRING:
                    self.client = AIProjectClient.from_connection_string(
                        conn_str=PROJECT_CONNECTION_STRING,
                        credential=fallback_credential
                    )
                else:
                    self.client = AIProjectClient(
                        credential=fallback_credential
                    )
            
            # Get the agent
            if AGENT_ID:
                self.agent = self.client.agents.get_agent(AGENT_ID)
                print(f"Using specified agent: {AGENT_ID}")
            else:
                # List available agents and use the first one
                agents = self.client.agents.list_agents()
                if agents.data:
                    self.agent = agents.data[0]
                    cl.user_session.set("agent_id", self.agent.id)
                    print(f"Using first available agent: {self.agent.id}")
                else:
                    raise Exception("No agents found in the project")
                    
            print(f"Successfully initialized Azure AI client with agent: {self.agent.id}")
            return True
            
        except Exception as e:
            print(f"Error initializing Azure AI client: {str(e)}")
            print("Make sure your system-assigned managed identity has the necessary permissions:")
            print("- Azure AI Developer role on the Azure AI project")
            print("- Contributor role on the resource group (if needed)")
            return False
    
    async def create_thread(self):
        """Create a new conversation thread"""
        try:
            self.thread = self.client.agents.create_thread()
            print(f"Created new thread: {self.thread.id}")
            return self.thread
        except Exception as e:
            print(f"Error creating thread: {str(e)}")
            return None
    
    async def send_message(self, message: str) -> str:
        """Send a message to the agent and get response"""
        try:
            if not self.thread:
                await self.create_thread()
            
            # Create a message in the thread
            message_obj = self.client.agents.create_message(
                thread_id=self.thread.id,
                role="user",
                content=message
            )
            
            # Create and run the assistant
            run = self.client.agents.create_run(
                thread_id=self.thread.id,
                assistant_id=self.agent.id
            )
            
            print(f"Created run: {run.id} with status: {run.status}")
            
            # Wait for the run to complete with timeout
            max_wait_time = 60  # 60 seconds timeout
            wait_time = 0
            
            while run.status in ["queued", "in_progress"] and wait_time < max_wait_time:
                await asyncio.sleep(1)
                wait_time += 1
                run = self.client.agents.get_run(
                    thread_id=self.thread.id,
                    run_id=run.id
                )
                print(f"Run status: {run.status}")
            
            if run.status == "completed":
                # Get the messages from the thread
                messages = self.client.agents.list_messages(
                    thread_id=self.thread.id
                )
                
                # Find the latest assistant message
                for msg in messages.data:
                    if msg.role == "assistant" and msg.created_at > message_obj.created_at:
                        return msg.content[0].text.value
                        
                return "I received your message but didn't generate a response."
                
            elif run.status == "failed":
                error_msg = getattr(run, 'last_error', {}).get('message', 'Unknown error')
                return f"I encountered an error processing your request: {error_msg}"
            elif wait_time >= max_wait_time:
                return "I'm taking longer than expected to respond. Please try again."
            else:
                return f"I'm currently {run.status}. Please try again in a moment."
            
        except Exception as e:
            print(f"Error sending message: {str(e)}")
            return f"I encountered an error: {str(e)}"

# Initialize the Azure AI agent
azure_agent = AzureAIAgent()

@cl.on_chat_start
async def start():
    """Initialize the chat session"""
    await cl.Message(
        content="🤖 Initializing Azure AI Foundry Agent with System-Assigned Managed Identity...",
        author="System"
    ).send()
    
    # Initialize Azure AI client
    if azure_agent.initialize():
        agent_info = f"Agent ID: {azure_agent.agent.id}" if azure_agent.agent else "Unknown agent"
        await cl.Message(
            content=f"✅ Connected to Azure AI Foundry using Managed Identity!\n\n{agent_info}\n\nHow can I help you today?",
            author="Assistant"
        ).send()
        
        # Store agent info in session
        cl.user_session.set("azure_agent", azure_agent)
        cl.user_session.set("initialized", True)
        
        # Create initial thread
        thread = await azure_agent.create_thread()
        if thread:
            cl.user_session.set("thread_id", thread.id)
            
    else:
        await cl.Message(
            content="""❌ Failed to connect to Azure AI Foundry using Managed Identity.

**Troubleshooting Steps:**
1. Ensure your Azure resource has a system-assigned managed identity enabled
2. Verify the managed identity has the required permissions:
   - **Azure AI Developer** role on the Azure AI project
   - **Contributor** role on the resource group (if needed)
3. Check that your environment variables are correctly set
4. For local development, ensure you're authenticated with `az login`

Please check your configuration and try again.""",
            author="System"
        ).send()
        cl.user_session.set("initialized", False)

@cl.on_message
async def main(message: cl.Message):
    """Handle incoming messages"""
    if not cl.user_session.get("initialized", False):
        await cl.Message(
            content="Please restart the chat to reinitialize the Azure AI connection.",
            author="System"
        ).send()
        return
    
    agent = cl.user_session.get("azure_agent")
    if not agent:
        await cl.Message(
            content="Azure AI agent not found. Please restart the chat.",
            author="System"
        ).send()
        return
    
    # Show typing indicator
    async with cl.Step(name="Processing with Azure AI Agent...") as step:
        step.input = message.content
        
        # Send message to Azure AI agent
        response = await agent.send_message(message.content)
        step.output = response
    
    # Send the response
    await cl.Message(
        content=response,
        author="Assistant"
    ).send()

@cl.on_chat_end
async def end():
    """Clean up when chat ends"""
    print("Chat session ended")

if __name__ == "__main__":
    cl.run()