import os
import chainlit as cl
from azure.ai.projects import AIProjectClient
from azure.identity import ManagedIdentityCredential, DefaultAzureCredential
from azure.core.exceptions import ClientAuthenticationError, HttpResponseError
import asyncio
import traceback
import logging
import sys
from typing import Optional

# Configure logging for Azure App Service
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.StreamHandler(sys.stderr)
    ]
)
logger = logging.getLogger(__name__)

# Force flush output
def log_and_print(message):
    """Log message and ensure it appears in both console and Azure logs"""
    print(message, flush=True)
    logger.info(message)
    sys.stdout.flush()
    sys.stderr.flush()

# Azure AI Foundry configuration
PROJECT_CONNECTION_STRING = os.getenv("AZURE_AI_PROJECT_CONNECTION_STRING")
AGENT_ID = os.getenv("AZURE_AI_AGENT_ID")
AZURE_SUBSCRIPTION_ID = os.getenv("AZURE_SUBSCRIPTION_ID")
AZURE_RESOURCE_GROUP = os.getenv("AZURE_RESOURCE_GROUP")
AZURE_PROJECT_NAME = os.getenv("AZURE_PROJECT_NAME")

class AzureAIAgent:
    def __init__(self):
        self.client = None
        self.agent = None
        self.thread = None
        self.debug_info = []  # Store debug info to show in UI
        
    def add_debug_info(self, message):
        """Add debug information that will be shown in UI"""
        self.debug_info.append(message)
        log_and_print(message)
        
    def get_debug_summary(self):
        """Get all debug information as a formatted string"""
        return "\n".join(self.debug_info)
        
    def initialize(self):
        """Initialize the Azure AI Project client and agent using system-assigned managed identity"""
        self.debug_info = []  # Reset debug info
        self.add_debug_info("=== Azure AI Foundry Initialization Debug Info ===")
        
        # Log environment variables (safely)
        self.add_debug_info(f"PROJECT_CONNECTION_STRING set: {bool(PROJECT_CONNECTION_STRING)}")
        if PROJECT_CONNECTION_STRING:
            # Show first 20 and last 10 characters for debugging
            masked = PROJECT_CONNECTION_STRING[:20] + "..." + PROJECT_CONNECTION_STRING[-10:]
            self.add_debug_info(f"Connection string preview: {masked}")
        
        self.add_debug_info(f"AGENT_ID: {AGENT_ID}")
        self.add_debug_info(f"AZURE_SUBSCRIPTION_ID set: {bool(AZURE_SUBSCRIPTION_ID)}")
        self.add_debug_info(f"AZURE_RESOURCE_GROUP: {AZURE_RESOURCE_GROUP}")
        self.add_debug_info(f"AZURE_PROJECT_NAME: {AZURE_PROJECT_NAME}")
        
        # Check Azure App Service environment
        website_instance_id = os.getenv("WEBSITE_INSTANCE_ID")
        website_site_name = os.getenv("WEBSITE_SITE_NAME")
        if website_instance_id:
            self.add_debug_info(f"Running in Azure App Service: {website_site_name}")
            self.add_debug_info(f"Instance ID: {website_instance_id}")
        else:
            self.add_debug_info("Not running in Azure App Service (local environment)")
        
        try:
            # Test managed identity credential first
            self.add_debug_info("\n--- Testing Managed Identity Credential ---")
            credential = ManagedIdentityCredential()
            
            # Try to get a token to test the credential
            try:
                # Use Azure Resource Manager scope to test credential
                self.add_debug_info("Attempting to get Azure Resource Manager token...")
                token = credential.get_token("https://management.azure.com/.default")
                self.add_debug_info(f"✅ Managed Identity credential working - Token expires: {token.expires_on}")
            except Exception as cred_error:
                self.add_debug_info(f"❌ Managed Identity credential test failed: {str(cred_error)}")
                self.add_debug_info(f"Error type: {type(cred_error).__name__}")
                
                # Try DefaultAzureCredential as fallback
                self.add_debug_info("\n--- Trying DefaultAzureCredential as fallback ---")
                credential = DefaultAzureCredential()
                try:
                    token = credential.get_token("https://management.azure.com/.default")
                    self.add_debug_info(f"✅ DefaultAzureCredential working - Token expires: {token.expires_on}")
                except Exception as default_cred_error:
                    self.add_debug_info(f"❌ DefaultAzureCredential also failed: {str(default_cred_error)}")
                    error_msg = f"Both credential types failed. Managed Identity: {cred_error}, Default: {default_cred_error}"
                    self.add_debug_info(error_msg)
                    raise Exception(error_msg)
            
            # Try AI-specific token scope
            self.add_debug_info("\n--- Testing AI-specific token scope ---")
            try:
                ai_token = credential.get_token("https://cognitiveservices.azure.com/.default")
                self.add_debug_info(f"✅ Got Cognitive Services token - expires: {ai_token.expires_on}")
            except Exception as ai_error:
                self.add_debug_info(f"⚠️ Cognitive Services token failed: {ai_error}")
            
            # Initialize the client
            self.add_debug_info("\n--- Initializing AI Project Client ---")
            if PROJECT_CONNECTION_STRING:
                self.add_debug_info("Using PROJECT_CONNECTION_STRING...")
                try:
                    # Updated syntax for latest azure-ai-projects library
                    self.client = AIProjectClient.from_connection_string(
                        PROJECT_CONNECTION_STRING,
                        credential
                    )
                    self.add_debug_info("✅ Client created with connection string")
                except Exception as client_error:
                    self.add_debug_info(f"❌ Failed to create client with connection string: {client_error}")
                    
                    # Try alternative initialization method
                    self.add_debug_info("Trying alternative initialization method...")
                    try:
                        # Parse connection string manually if needed
                        parts = {}
                        for part in PROJECT_CONNECTION_STRING.split(';'):
                            if '=' in part:
                                key, value = part.split('=', 1)
                                parts[key] = value
                        
                        endpoint = parts.get('Endpoint', '')
                        subscription_id = parts.get('SubscriptionId', '')
                        resource_group = parts.get('ResourceGroupName', '')
                        project_name = parts.get('ProjectName', '')
                        
                        self.add_debug_info(f"Parsed - Endpoint: {endpoint[:50]}...")
                        self.add_debug_info(f"Parsed - SubscriptionId: {subscription_id}")
                        self.add_debug_info(f"Parsed - ResourceGroup: {resource_group}")
                        self.add_debug_info(f"Parsed - ProjectName: {project_name}")
                        
                        # Try creating client with parsed parameters
                        self.client = AIProjectClient(
                            endpoint=endpoint,
                            credential=credential,
                            subscription_id=subscription_id,
                            resource_group_name=resource_group,
                            project_name=project_name
                        )
                        self.add_debug_info("✅ Client created with parsed connection string")
                        
                    except Exception as parse_error:
                        self.add_debug_info(f"❌ Failed to parse connection string: {parse_error}")
                        raise client_error
                        
            elif all([AZURE_SUBSCRIPTION_ID, AZURE_RESOURCE_GROUP, AZURE_PROJECT_NAME]):
                self.add_debug_info("Using individual Azure parameters...")
                try:
                    # Updated syntax for individual parameters
                    self.client = AIProjectClient(
                        credential=credential,
                        subscription_id=AZURE_SUBSCRIPTION_ID,
                        resource_group_name=AZURE_RESOURCE_GROUP,
                        project_name=AZURE_PROJECT_NAME
                    )
                    self.add_debug_info("✅ Client created with individual parameters")
                except Exception as client_error:
                    self.add_debug_info(f"❌ Failed to create client with individual parameters: {client_error}")
                    raise
            else:
                error_msg = "Missing required configuration: either PROJECT_CONNECTION_STRING or all of (AZURE_SUBSCRIPTION_ID, AZURE_RESOURCE_GROUP, AZURE_PROJECT_NAME)"
                self.add_debug_info(f"❌ {error_msg}")
                raise Exception(error_msg)
            
            # Test client by trying to list agents
            self.add_debug_info("\n--- Testing Client Connection ---")
            try:
                self.add_debug_info("Attempting to list agents...")
                agents = self.client.agents.list_agents()
                self.add_debug_info(f"✅ Successfully connected - Found {len(agents.data)} agents")
                
                # List all agents for debugging
                for i, agent in enumerate(agents.data):
                    self.add_debug_info(f"  Agent {i+1}: {agent.id}")
                
                # Get the agent
                if AGENT_ID:
                    self.add_debug_info(f"Looking for specified agent: {AGENT_ID}")
                    self.agent = self.client.agents.get_agent(AGENT_ID)
                    self.add_debug_info(f"✅ Using specified agent: {AGENT_ID}")
                else:
                    if agents.data:
                        self.agent = agents.data[0]
                        self.add_debug_info(f"✅ Using first available agent: {self.agent.id}")
                        cl.user_session.set("agent_id", self.agent.id)
                    else:
                        error_msg = "No agents found in the project"
                        self.add_debug_info(f"❌ {error_msg}")
                        raise Exception(error_msg)
                        
                self.add_debug_info(f"✅ Successfully initialized Azure AI client with agent: {self.agent.id}")
                return True
                
            except HttpResponseError as http_error:
                self.add_debug_info(f"❌ HTTP Error accessing agents: {http_error}")
                self.add_debug_info(f"Status Code: {http_error.status_code}")
                self.add_debug_info(f"Error Code: {http_error.error.code if hasattr(http_error, 'error') else 'N/A'}")
                self.add_debug_info(f"Error Message: {http_error.message}")
                raise
            except Exception as client_error:
                self.add_debug_info(f"❌ Error testing client connection: {str(client_error)}")
                self.add_debug_info(f"Error type: {type(client_error).__name__}")
                self.add_debug_info(f"Traceback: {traceback.format_exc()}")
                raise
                
        except ClientAuthenticationError as auth_error:
            self.add_debug_info(f"\n❌ Authentication Error: {str(auth_error)}")
            self.add_debug_info("This typically means:")
            self.add_debug_info("1. Managed Identity is not enabled on the App Service")
            self.add_debug_info("2. Managed Identity doesn't have required permissions")
            self.add_debug_info("3. The Azure AI project resource is not accessible")
            return False
            
        except HttpResponseError as http_error:
            self.add_debug_info(f"\n❌ HTTP Response Error: {str(http_error)}")
            self.add_debug_info(f"Status Code: {http_error.status_code}")
            if hasattr(http_error, 'error'):
                self.add_debug_info(f"Error Code: {http_error.error.code}")
                self.add_debug_info(f"Error Message: {http_error.error.message}")
            self.add_debug_info("\nThis typically means:")
            self.add_debug_info("1. Resource not found (check subscription ID, resource group, project name)")
            self.add_debug_info("2. Insufficient permissions on the Azure AI project")
            self.add_debug_info("3. Network connectivity issues")
            return False
            
        except Exception as e:
            self.add_debug_info(f"\n❌ Unexpected Error: {str(e)}")
            self.add_debug_info(f"Error Type: {type(e).__name__}")
            self.add_debug_info(f"Full traceback:\n{traceback.format_exc()}")
            self.add_debug_info("\nDetailed error information:")
            self.add_debug_info("Make sure your system-assigned managed identity has the necessary permissions:")
            self.add_debug_info("- Azure AI Developer role on the Azure AI project")
            self.add_debug_info("- Contributor role on the resource group (if needed)")
            return False
    
    async def create_thread(self):
        """Create a new conversation thread"""
        try:
            self.thread = self.client.agents.create_thread()
            log_and_print(f"Created new thread: {self.thread.id}")
            return self.thread
        except Exception as e:
            log_and_print(f"Error creating thread: {str(e)}")
            log_and_print(f"Error type: {type(e).__name__}")
            log_and_print(f"Full traceback:\n{traceback.format_exc()}")
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
            
            log_and_print(f"Created run: {run.id} with status: {run.status}")
            
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
                log_and_print(f"Run status: {run.status}")
            
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
            log_and_print(f"Error sending message: {str(e)}")
            log_and_print(f"Error type: {type(e).__name__}")
            log_and_print(f"Full traceback:\n{traceback.format_exc()}")
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
    init_result = azure_agent.initialize()
    
    # ALWAYS show debug information, regardless of success or failure
    debug_summary = azure_agent.get_debug_summary()
    await cl.Message(
        content=f"**🔍 Debug Information:**\n```\n{debug_summary}\n```",
        author="System"
    ).send()
    
    if init_result:
        agent_info = f"Agent ID: {azure_agent.agent.id}" if azure_agent.agent else "Unknown agent"
        await cl.Message(
            content=f"✅ **Connected to Azure AI Foundry using Managed Identity!**\n\n{agent_info}\n\nHow can I help you today?",
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
            content=f"""❌ **Failed to connect to Azure AI Foundry using Managed Identity.**

**Please review the debug information above for specific error details.**

**Quick Troubleshooting Checklist:**

🔧 **Identity & Permissions:**
- [ ] System-assigned managed identity enabled in App Service → Identity
- [ ] **Azure AI Developer** role assigned to managed identity on Azure AI project
- [ ] **Cognitive Services OpenAI User** role assigned (if using OpenAI models)
- [ ] App Service restarted after enabling managed identity

🌐 **Configuration:**
- [ ] PROJECT_CONNECTION_STRING is correctly formatted
- [ ] Azure AI project exists and is accessible
- [ ] Network connectivity allows access to Azure AI services

💡 **Next Steps:**
1. Check the debug information above for the exact error
2. Verify role assignments in Azure portal
3. Ensure managed identity is properly configured
4. Try restarting the App Service

Please address the issues shown in the debug output and try again.""",
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
    log_and_print("Chat session ended")

if __name__ == "__main__":
    cl.run()