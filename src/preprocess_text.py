import re
import string
import nltk
from env import stopwords_spanish
from nltk.stem import WordNetLemmatizer
from nltk.corpus.reader import wordnet as wn
from collections import defaultdict

# Initialize constants
STOPWORDS = stopwords_spanish
PUNCTUATION = set(string.punctuation)
SPACES_PATTERN = re.compile(' +')
NUMBERS_LETTERS_PATTERN = re.compile(r'([0-9]+)([a-zA-Z]+)')

LEMMATIZER = WordNetLemmatizer()
TAG_MAP = defaultdict(lambda: wn.NOUN)
TAG_MAP['J'] = wn.ADJ
TAG_MAP['V'] = wn.VERB
TAG_MAP['R'] = wn.ADV


def preprocess(text):
    """
    Preprocess Spanish text block to delete stopwords, punctuation, extra spaces, lower case, separate numbers and letters
    and lemmatize the words
    :param text:
    :return:
    """

    # Tokenize and filter out stopwords and punctuation
    words = [word for word in text.split() if word.lower() not in STOPWORDS and not set(word) & PUNCTUATION]

    # Convert to lowercase
    text = " ".join(words).lower()

    # Replace multiple spaces with a single space
    text = SPACES_PATTERN.sub(' ', text)

    # Separate numbers and letters
    text = NUMBERS_LETTERS_PATTERN.sub(r'\1 \2', text)

    # Lemmatize words
    final_text = []
    for word, tag in nltk.pos_tag(text.split()):
        if word.isalpha():
            word_final = LEMMATIZER.lemmatize(word, TAG_MAP[tag[0]])
            final_text.append(word_final)

    return " ".join(final_text)
