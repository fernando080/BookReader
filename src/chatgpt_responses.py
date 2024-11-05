import os
import re
from typing import List
from env import path_to_listen as path

import openai
import tiktoken
from dotenv import load_dotenv  # This is to load the .env file
from langchain.chains import ConversationChain
# from langchain.memory.buffer import ConversationBufferMemory
from langchain.chains.conversation.prompt import ENTITY_MEMORY_CONVERSATION_TEMPLATE
from langchain.chat_models import ChatOpenAI
from langchain.memory.entity import ConversationEntityMemory

from env import MAX_TOKENS, TOKENS_LIMIT, MODEL, PAGE_LIMIT
from env import BM25_threshold, encabezado
from infomation_retrival_for_questions import read_files, preprocess, get_most_relevant_docs


def create_conversation_chain(inputs, num_msgs=3):
    """
    Creates the base instance for the conversation with the llm and the memory
    :param num_msgs: Number of messages to include in the memory buffer
    :return: The conversation chain instance
    """
    load_dotenv()

    llm = ChatOpenAI(
        temperature=0,
        model_name=MODEL,
        verbose=False,
    )
    memory = ConversationEntityMemory(
        llm=llm,
        k=num_msgs,
    )

    if inputs:
        for inp in inputs:
            memory.save_context(inp[0], inp[1])

    conversation = ConversationChain(
        llm=llm,
        memory=memory,
        prompt=ENTITY_MEMORY_CONVERSATION_TEMPLATE,
        verbose=True,
    )
    return conversation


def get_chunks(lst: List[str], n: int):
    """Yield n-sized chunks from lst."""
    for i in range(0, len(lst), n):
        yield lst[i:i + n]


def initialize_conversation():
    msgs = [
        {"role": "system", "content": "You are a helpful marketing teacher."},
    ]
    return msgs


def compose_input_with_relevant_info(path, relevant_info, prefix_info_phrase = "Resume detalladamente el texto con el que poder responder cualquier pregunta y genera una lista de ideas principales: "):
    not_found_info = False
    tokenizer = tiktoken.encoding_for_model(MODEL)
    acc_tokens = len(tokenizer.encode(prefix_info_phrase))
    total_tokens = acc_tokens
    msgs_content = ""
    list_of_input_msgs = []

    # Sort the relevant info by score and get the top PAGE_LIMIT
    relevant_info = sorted(relevant_info, key=lambda x: x[1], reverse=True)[:PAGE_LIMIT]

    for filename, score in relevant_info:
        if score < BM25_threshold:
            print(f"File {filename} has a BM25 score lower than the threshold. Skipping...")
            continue
        doc_folder = re.sub(r"_\d+\.txt$", "", filename)
        try:
            with open(os.path.join(path, doc_folder, filename), 'r', encoding="utf-8") as file:
                info = file.read()
            info = preprocess(info)
            info = info + "\n"
            info_tokens = len(tokenizer.encode(info))
            if acc_tokens + info_tokens < MAX_TOKENS:
                acc_tokens += info_tokens
                total_tokens += info_tokens
                print(f"Adding {filename} to the prompt. Accumulated tokens: {acc_tokens}")
                msgs_content += info  # Add each file to the prompt
            else:
                print(f'Prompt is full. Tokens: {acc_tokens}.\n')
                print(f"total tokens: {total_tokens}")
                acc_tokens = info_tokens + len(
                    tokenizer.encode(prefix_info_phrase + '"'))  # Reset the token count for the next prompt
                print(f"Starting a new message with {filename}. Tokens: {acc_tokens}")
                list_of_input_msgs.append(
                    prefix_info_phrase + msgs_content + '"')  # Add the previous prompt to the list of prompts
                msgs_content = info  # Start a new prompt with the current file
        except Exception as e:
            print(f"Error reading file {filename}: {e}")

    # If we have detected relevant info but we have not reached the token limit, add the last prompt to the list
    if msgs_content and not list_of_input_msgs:
        list_of_input_msgs.append(prefix_info_phrase + msgs_content + '"')

    # If we have not detected relevant info, add the default prompt to the list
    if not list_of_input_msgs:
        not_found_info = True

    return list_of_input_msgs, not_found_info, total_tokens


def add_relevant_info(path, msgs, relevant_info, question):
    tokenizer = tiktoken.encoding_for_model(MODEL)
    acc_tokens: int = len(tokenizer.encode(question)) + len(msgs[0]['content'])
    msgs_content = ""
    for filename, _ in relevant_info[::-1]:  # Reverse the list to add the most relevant text at the final prompt
        doc_folder = re.sub(r"_\d+\.txt$", "", filename)
        try:
            with open(os.path.join(path, doc_folder, filename), 'r', encoding="utf-8") as file:
                info = file.read()
            info = preprocess(info)
            info = info + "\n"
            info_tokens = len(tokenizer.encode(info))
            if acc_tokens + info_tokens < MAX_TOKENS:
                acc_tokens += info_tokens
                print(f"Adding {filename} to the prompt. Accumulated tokens: {acc_tokens}")
                msgs_content += info
            else:
                break
        except Exception as e:
            print(f"Error reading file {filename}: {e}")

    msgs.append({"role": "assistant", "content": msgs_content})
    return msgs, acc_tokens


def add_user_question(msgs, question, enc=encabezado):
    msgs.append({"role": "user",
                 "content": enc + question})
    return msgs


def generate_response(msgs, added_tokens=0, tolerance=10):
    """
    Generates a response from GPT-3
    :param msgs: List of messages in the conversation
    :param added_tokens: Tokens added to the prompt
    :param tolerance: Tolerance for the token limit
    :return: The response from GPT-3
    """
    tokenizer = tiktoken.encoding_for_model(MODEL)
    print(f"Generating response. Accumulated tokens: {len(tokenizer.encode(msgs[1]['content']))}")
    response = openai.ChatCompletion.create(
        model=MODEL,
        messages=msgs,
        max_tokens=TOKENS_LIMIT - added_tokens - tolerance
    )
    return response.choices[0].message["content"]


def process_response(response):
    # Here you can add any post-processing steps you want to apply to the response before returning it
    return response.strip()


def chatgpt_response(question, slides_directory):
    """
    Function to get the response from GPT-3 for a given question
    :param slides_directory: directory with the files needed to answer the question
    :param question: Question asked by the user
    :return: The response from GPT-3
    """
    # Initialize the conversation
    msgs = initialize_conversation()
    # Read the files in the directory
    corpus, corpus_tokenized, filenames = read_files(slides_directory)
    # Get the most relevant documents
    relevant_info = get_most_relevant_docs(question, corpus_tokenized, filenames)
    # Compose the input with the relevant info
    msgs, added_tokens = add_relevant_info(path, msgs, relevant_info, question)
    # Add the user question to the conversation
    msgs = add_user_question(msgs, question)
    # Generate the response
    try:
        response = generate_response(msgs, added_tokens)
    except openai.error.InvalidRequestError:
        response = generate_response(msgs, added_tokens, tolerance=50)
    # Process the response
    response = process_response(response)
    return response
