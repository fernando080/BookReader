import os
import tiktoken
import numpy as np

from rank_bm25 import BM25Okapi
from env import retrival_threshold, MODEL, path_to_listen
from preprocess_text import preprocess

tokenizer_BM25 = tiktoken.encoding_for_model(MODEL)


# Function to tokenize text for BM25
def tokenize_text(text):
    text = preprocess(text)
    return tokenizer_BM25.encode(text)


# Function to tokenize text for BERT embeddings
def read_files(pdf_foldername, user_id):
    corpus = []
    corpus_tokenized = []
    filenames = []
    folder_path = os.path.join(path_to_listen, user_id, pdf_foldername)
    for filename in os.listdir(folder_path):
        if str(filename).endswith(".txt"):
            with open(os.path.join(folder_path, filename), 'r', encoding='utf-8') as file:
                text = file.read()
                corpus.append(text)
                # To use BERT embeddings, uncomment the following line
                # corpus_tokenized.append(get_embedding(text))
                # To use BM25, uncomment the following line
                corpus_tokenized.append(tokenize_text(text))
                filenames.append(filename)
    return corpus, corpus_tokenized, filenames


# Function to compute BM25 similarity
def compute_bm25_similarity(raw_query, corpus_tokenized):
    query = tokenize_text(raw_query)
    bm25 = BM25Okapi(corpus_tokenized)
    doc_scores = bm25.get_scores(query)

    return doc_scores


# Function to get the most relevant documents
def get_most_relevant_docs(raw_query, embeddings, filenames):
    doc_scores = compute_bm25_similarity(raw_query, embeddings)

    sorted_doc_ids = np.argsort(doc_scores)[::-1]
    sorted_filenames = [filenames[i] for i in sorted_doc_ids]
    sorted_scores = [doc_scores[i] for i in sorted_doc_ids]

    best_scored = list(zip(sorted_filenames, sorted_scores))[0]
    relevant_docs = []

    for i in zip(sorted_filenames, sorted_scores):
        if i[1]/best_scored[1] > retrival_threshold:
            relevant_docs.append(i)
        else:
            return relevant_docs
