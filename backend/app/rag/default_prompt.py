DEFAULT_INTENT_GRAPH_KNOWLEDGE = """\
Given a list of prerequisite questions and their relevant knowledge for the user's main question, when conflicts in meaning arise, prioritize the relationship with the higher weight and the more recent version.

Knowledge sub-queries:

{% for sub_query, data in sub_queries.items() %}

Sub-query: {{ sub_query }}

  - Entities:

{% for entity in data['entities'] %}

    - Name: {{ entity.name }}
    - Description: {{ entity.description }}

{% endfor %}

  - Relationships:

{% for relationship in data['relationships'] %}

    - Description: {{ relationship.rag_description }}
    - Weight: {{ relationship.weight }}

{% endfor %}

{% endfor %}
"""

DEFAULT_NORMAL_GRAPH_KNOWLEDGE = """\
Given a list of relationships of a knowledge graph as follows. When there is a conflict in meaning between knowledge relationships, the relationship with the higher `weight` and newer `last_modified_at` value takes precedence.

---------------------
Entities:

{% for entity in entities %}

- Name: {{ entity.name }}
- Description: {{ entity.description }}

{% endfor %}

---------------------

Knowledge relationships:

{% for relationship in relationships %}

- Description: {{ relationship.rag_description }}
- Weight: {{ relationship.weight }}
- Last Modified At: {{ relationship.last_modified_at }}
- Meta: {{ relationship.meta | tojson(indent=2) }}

{% endfor %}
"""

DEFAULT_CLARIFYING_QUESTION_PROMPT = """\
---------------------
The prerequisite questions and their relevant knowledge for the user's main question.
---------------------

{{graph_knowledges}}

---------------------

Task:
Given the conversation between the user and ASSISTANT, along with the follow-up message from the user, and the provided prerequisite questions and relevant knowledge, determine if the user's question is clear and specific enough for a confident response. If the question lacks necessary details or context, identify the specific ambiguities and generate a clarifying question to address them.

Instructions:
1. Assess Information Sufficiency:
   - Evaluate if the user’s question provides enough detail to generate a precise answer based on the prerequisite questions, relevant knowledge, and conversation history.
   - If the user's question is too vague or lacks key information, identify what additional information would be necessary for clarity.

2. Generate a Clarifying Question:
   - If the question is clear and answerable, return exact "False" as the response.
   - If clarification is needed, return a specific question to ask the user, directly addressing the information gap. Avoid general questions; focus on the specific details required for an accurate answer.

3. Use the same language to ask the clarifying question as the user's original question.

Example 1:

user: "Does TiDB support foreign keys?"
Relevant Knowledge: TiDB supports foreign keys starting from version 6.6.0.

Response:

Which version of TiDB are you using?

Example 2:

user: "Does TiDB support nested transaction?"
Relevant Knowledge: TiDB supports nested transaction starting from version 6.2.0.

Response:

Which version of TiDB are you using?

Example 3:

user: "Does TiDB support foreign keys? I'm using TiDB 6.5.0."
Relevant Knowledge: TiDB supports foreign keys starting from version 6.6.0.

Response:

False

Your Turn:

Chat history:

{{chat_history}}

---------------------

Follow-up question:

{{question}}

Response:
"""

DEFAULT_CONDENSE_QUESTION_PROMPT = """\
Current Date: {{current_date}}
---------------------
The prerequisite questions and their relevant knowledge for the user's main question.
---------------------

{{graph_knowledges}}

---------------------

Task:
Given the conversation between the Human and Assistant, along with the follow-up message from the Human, and the provided prerequisite questions and relevant knowledge, refine the Human’s follow-up message into a standalone, detailed question.

Instructions:
1. Focus on the latest query from the Human, ensuring it is given the most weight.
2. Incorporate Key Information:
  - Use the prerequisite questions and their relevant knowledge to add specific details to the follow-up question.
  - Replace ambiguous terms or references in the follow-up question with precise information from the provided knowledge. Example: Replace “latest version” with the actual version number mentioned in the knowledge.
3. Utilize Conversation Context:
  - Incorporate relevant context and background information from the conversation history to enhance the question's specificity.
4. Optimize for Retrieval:
  - Ensure the refined question emphasizes specific and relevant terms to maximize the effectiveness of a vector search for retrieving precise and comprehensive information.
5. Grounded and Factual:
  - Make sure the refined question is grounded in and directly based on the user's follow-up question and the provided knowledge.
  - Do not introduce information that is not supported by the knowledge or conversation history.
6. Give the language hint for the answer:
  - Add a hint after the question like "(Answer language: English)", or "(Answer language: Chinese)", etc.
  - This language hint should be exactly same with the language of the original question.
  - If the original question has part of other language aside from English, please use the language of another language rather than English. Example: "tidb tableread慢会是哪些原因", it should be Chinese.

Example:

Chat History:

Human: "I'm interested in the performance improvements in the latest version of TiDB."
Assistant: "TiDB version 8.1 was released recently with significant performance enhancements over version 6.5."

Follow-up Question:

"Can you tell me more about these improvements?"

Prerequisite Questions and Relevant Knowledge:

- Prerequisite Question: What is the latest version of TiDB?
- Relevant Knowledge: The latest version of TiDB is 8.1.

...

Refined Standalone Question:

"Can you provide detailed information about the performance improvements introduced in TiDB version 8.1 compared to version 6.5? (Answer language: English)"

Your Turn:

Chat history:

{{chat_history}}

---------------------

Followup question:

{{question}}

---------------------

Refined standalone question:
"""


DEFAULT_TEXT_QA_PROMPT = """\
Current Date: {{current_date}}
---------------------
Knowledge graph information is below
---------------------

{{graph_knowledges}}

---------------------
Context information is below.
---------------------

<<context_str>>

---------------------

Answer Format:

Use markdown footnote syntax (for example: [^1]) to indicate sources you used.
Each footnote must correspond to a unique source. Do not use the same source for multiple footnotes.

### Examples of Correct Footnote Usage (no the unique sources and diverse sources):
[^1]: [TiDB Overview | PingCAP Docs](https://docs.pingcap.com/tidb/stable/overview)
[^2]: [TiDB Architecture | PingCAP Docs](https://docs.pingcap.com/tidb/stable/architecture)

### Examples of Incorrect Footnote Usage (Avoid duplicating the same source for multiple footnotes):
[^1]: [TiDB Introduction | PingCAP Docs](https://docs.pingcap.com/tidb/v5.4/overview)
[^2]: [TiDB Introduction | PingCAP Docs](https://docs.pingcap.com/tidb/v5.4/overview)
[^3]: [TiDB Introduction | PingCAP Docs](https://docs.pingcap.com/tidb/dev/overview)
[^4]: [TiDB Introduction | PingCAP Docs](https://docs.pingcap.com/tidb/stable/overview)

---------------------

Answer Language:

Follow the language of the language hint after the Refined Question.
If the language hint is not provided, use the language that the original questions used.

---------------------

As a customer support assistant, please do not fabricate any knowledge. If you cannot get knowledge from the context, please just directly state "you do not know", rather than constructing nonexistent and potentially fake information!!!

First, analyze the provided context information without assuming prior knowledge. Identify all relevant aspects of knowledge contained within. Then, from various perspectives and angles, answer questions as thoroughly and comprehensively as possible to better address and resolve the user's issue.

The Original questions is:

{{original_question}}

The Refined Question used to search:
<<query_str>>

Answer:
"""

DEFAULT_REFINE_PROMPT = """\
The Original questions is:

{{original_question}}

Refined Question used to search:
<<query_str>>

---------------------
We have provided an existing answer:
---------------------

<<existing_answer>>

---------------------
We have the opportunity to refine the existing answer (only if needed) with some more knowledge graph and context information below.

---------------------
Knowledge graph information is below
---------------------

{{graph_knowledges}}

---------------------
Context information is below.
---------------------

<<context_msg>>

---------------------
Given the new context, refine the original answer to better answer the query. If the context isn't useful, return the original answer.
And the answer should use the same language with the question. If the answer has different language with the original question, please translate it to the same language with the question.

Refined Answer:
"""

DEFAULT_FURTHER_QUESTIONS_PROMPT = """\
The chat message content is:

{{chat_message_content}}

---------------------
Task:
Based on the provided chat message, generate 3–5 follow-up questions that are relevant to the content. Each question should explore the topic in greater detail, seek clarification, or introduce new angles for discussion.

Instructions:
1. Build upon the key information, themes, or insights within the provided chat message.
2. Aim for variety in question type (clarifying, probing, or exploratory) to encourage a deeper conversation.
3. Ensure each question logically follows from the context of the provided chat message.
4. Keep questions concise yet insightful to maximize engagement.
5. Use the same language with the chat message content.
6. Each question should end with a question mark.
7. Each question should be in a new line, DO NOT add any indexes or blank lines, just output the questions.

Now, generate 3–5 follow-up questions below:
"""

DEFAULT_GENERATE_GOAL_PROMPT = """\
Given the conversation history between the User and Assistant, along with the latest follow-up question from the User, perform the following tasks:

1. **Language Detection**:
    - Analyze the User's follow-up question to determine the language used.

2. **Context Classification**:
    - **Determine Relevance to TiDB**:
        - Assess whether the follow-up question is related to TiDB products, support, or any TiDB-related context.
    - **Set Background Accordingly**:
        - **If Related to TiDB**:
            - Set the background to encompass the relevant TiDB context. This may include aspects like TiDB features, configurations, best practices, troubleshooting, or general consulting related to TiDB.
            - Example backgrounds:
                - "TiDB product configuration and optimization."
                - "TiDB troubleshooting and support."
                - "TiDB feature consultation."
        - **If Unrelated to TiDB**:
            - Set the background to "Other topics."

3. **Goal Generation**:
    - **Clarify Intent to Avoid Ambiguity**:
        - **Instructional Guidance**:
            - If the User's question seeks guidance or a method (e.g., starts with "How to"), ensure the goal reflects a request for a step-by-step guide or best practices.
        - **Information Retrieval**:
            - If the User's question seeks specific information or confirmation (e.g., starts with "Can you" or "Is it possible"), rephrase it to focus on providing the requested information or verification without implying that the assistant should perform any actions.
            - **Important**: Do not interpret these questions as requests for the assistant to execute operations. Instead, understand whether the user seeks to confirm certain information or requires a proposed solution, and restrict responses to information retrieval and guidance based on available documentation.
    - **Reformulate the Latest User Follow-up Question**:
        - Ensure the question is clear, directive, and suitable for a Q&A format.
    - **Specify Additional Details**:
        - **Detected Language**: Clearly indicate the language.
        - **Desired Answer Format**: Specify if the answer should be in text, table, code snippet, etc.
        - **Additional Requirements**: Include any other necessary instructions to tailor the response appropriately.

4. **Output**:
    - Produce a goal string in the following format:
      "[Refined Question] (Lang: [Detected Language], Format: [Format], Background: [Specified Goal Scenario])"

**Examples**:

**Example 1**:

Chat history:

[]

Follow-up question:

"tidb encryption at rest 会影响数据压缩比例吗？"

Goal:

Does encryption at rest in TiDB affect the data compression ratio? (Lang: Chinese, Format: text, Background: TiDB product related consulting.)

---------------------

**Example 2**:

Chat history:

[]

Follow-up question:

"干嘛的？"

Goal:

What can you do? (Lang: Chinese, Format: text, Background: General inquiry about the assistant's capabilities.)

---------------------

**Example 3**:

Chat history:

[]

Follow-up question:

"oracle 怎么样？"

Goal:

How is Oracle? (Lang: Chinese, Format: text, Background: Other topics.)

---------------------

**Example 4**:

Chat history:

[]

Follow-up question:

"Why is TiDB Serverless up to 70% cheaper than MySQL RDS? (use a table if possible)"

Goal:

Why is TiDB Serverless up to 70% cheaper than MySQL RDS? Please provide a comparison in a table format if possible. (Lang: English, Format: table, Background: Cost comparison between TiDB Serverless and MySQL RDS.)

---------------------

**Example 5 (Enhanced for Clarity and Guidance)**:

Chat history:

[]

Follow-up question:

"能否找到 tidb 中哪些视图的定义中包含已经被删除的表？"

Goal:

How to find which views in TiDB have definitions that include tables that have been deleted? (Lang: Chinese, Format: text, Background: TiDB product related consulting.)

---------------------

**Your Task**:

Chat history:

{{chat_history}}

Follow-up question:

{{question}}

Goal:
"""

DEFAULT_ANALYZE_QUESTION_AND_ENHANCE_PROMPT = """
Determine if the following question belongs to sales/pre-sales related consultation. The question is proposed by internal sales, pre-sales, or other related personnel who are preparing for customer communication or handling customer inquiries.

Sales/pre-sales related questions are characterized by:
1. Preparation questions for better serving customers
2. Focus on the "Persona (customer persona) - Pain Point (customer pain point) - Feature (product feature)" framework
3. Seek the best way to handle customer inquiries
4. Collect competitive information
5. Improve the effectiveness of sales/pre-sales work

Common scenarios include but are not limited to:

1. Persona (customer persona) related:
   - The focus and decision factors of specific industry customers
   - The demand differences of different roles (e.g., CTO, architect, DBA)
   - The typical needs of different size enterprises
   - The technical stack and architecture characteristics of target customers
   Examples: "What are the typical needs of internet companies?"

2. Pain Point (customer pain point) related:
   - The common business pain points in the industry
   - The typical technical challenges in different scenarios
   - The difficulties faced by customers in upgrading
   - The problems in operation, cost, and performance
   Examples: "What is the biggest technical challenge for e-commerce customers during Double 11?"

3. Feature (product feature) related:
   - The mapping relationship between product features and customer pain points
   - The differentiated competitive advantages of features
   - The best practices and typical scenarios of features
   - The application value of new features
   Examples: "How does our distributed transaction feature solve the pain points of the financial industry?"

4. Solution consultation:
   - Verify the feasibility of specific solutions
   - The differences in solutions for different personas
   - The design of solutions combining pain points
   - The best practices of feature combinations
   Examples: "What is the best architecture for the transformation of traditional bank core systems?"

5. Business related:
   - The pricing strategies for different size customers
   - The ROI analysis for specific pain points
   - The authorization方式 of feature modules
   - The implementation cycle and resource planning
   Examples: "What is the typical budget for a complete solution for the financial industry?"

6. Competitor response:
   - The competitive strategies for different customer personas
   - The differentiated advantages for specific pain points
   - The competitive comparison at the feature level
   - The competitive analysis at the solution level
   Examples: "What is the core advantage of our solution compared to competitor A in the financial scenario?"

In contrast, non-sales related internal issues are usually:
1. Focus on specific technical implementation details
2. About the use or maintenance of internal systems
3. Daily work process consultation
Examples: "How to configure the parameters of the test environment?"

Please return the analysis result in the following JSON format:
{
    "is_sales_related": true/false,  # Whether the question is related to sales
    "enhanced_question": "",         # The enhanced question, supplemented with necessary context and analysis
    "related_aspects": []            # The related aspects to consider together
}

Question: {{question}}
"""