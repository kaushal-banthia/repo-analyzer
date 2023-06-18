import subprocess
import os
from github import Github
from langchain.llms import OpenAI
from flask import Flask, request, render_template, session, redirect, url_for
from constants import SECRET_KEY, FILE_EXTENSIONS, GITHUB_TOKEN, OPEN_AI_API_KEY
import tiktoken


app = Flask(__name__)
app.secret_key = SECRET_KEY

# Set up GitHub authentication
g = Github(GITHUB_TOKEN)

repositories = []
responses = {}

def num_tokens(string, encoding_name = "gpt2"):
    """Returns the number of tokens in a text string."""
    encoding = tiktoken.get_encoding(encoding_name)
    num_tokens = len(encoding.encode(string))
    return num_tokens

# Fetch a user's repositories from GitHub
def fetch_user_repositories(github_url):
    username = github_url.split("/")[-1]
    user = g.get_user(username)
    repositories = [repo for repo in user.get_repos()]
    return repositories

# Clone a repository or download it as a ZIP file
def download_repository(repository):
    try:
        subprocess.run(["git", "clone", repository.clone_url])
        return True
    except:
        # Handle errors
        return False
    
def get_answer_from_gpt(prompt, tokens=5):
    llm = OpenAI(openai_api_key=OPEN_AI_API_KEY, temperature=0.2, max_tokens=tokens, top_p=1.0, frequency_penalty=0.0, presence_penalty=0.0)
    answer = llm.predict(prompt).strip()
    return answer

def preprocess(file_content):
    # break the file into chunks of 3500 toekens
    if file_content.encoding == 'base64':
        text = file_content.decoded_content.decode('latin-1')
    else:
        text = file_content.content

    chunks = []
    chunk = ""
    for word in text.split():
        if num_tokens(chunk) < 3500:
            chunk += word + " "
        else:
            chunks.append(chunk)
            chunk = ""
    chunks.append(chunk)
    
    # save the chunks as separate files
    score = 0
    for chunk in chunks:
        prompt = "Assign a technical complexity score to the following code on a scale of 1 to 10 and reply with nothing else. You do not have to put in any corrections fo the code. Just reply with a number only. Make sure to keep the answer only numeric:\n " + chunk + "\n Score: "
        score += float(get_answer_from_gpt(prompt))
    return score/len(chunks)

def get_files_from_repo(repo, explanation = 0):
    score = 0
    contents = repo.get_contents("")
    code_chunks = []
    chunk_score = []
    while contents:
        file_content = contents.pop(0)
        if file_content.type == "dir":
            contents.extend(repo.get_contents(file_content.path))
            # check if a folder named the same exists
            if not os.path.exists(file_content.path):
                # create a folder by the name of the directory
                os.mkdir(file_content.path)
        else:
            for ext in FILE_EXTENSIONS:
                if file_content.path.endswith(ext):
                    if file_content.encoding == 'base64':
                        text = file_content.decoded_content.decode('latin-1')
                    else:
                        text = file_content.content
                    if num_tokens(text) < 3500:
                        # save the file
                        prompt = "Assign a technical complexity score to the following code on a scale of 1 to 10 and reply with nothing else. You do not have to put in any corrections fo the code. Just reply with a number only. Make sure to keep the answer only numeric:\n " + text + "\n Score: "
                        score += float(get_answer_from_gpt(prompt))
                    else:
                        score += preprocess(file_content)
                    if explanation == 1:
                        code_chunks.append(text)
                        chunk_score.append(score)
                    break

    if explanation == 1:
        return code_chunks, chunk_score, score
    return score

@app.route('/')
def execute():
    return render_template('form.html')

@app.route('/', methods=['POST'])
def my_form_post():
    github_url = request.form['text'].strip()
    if github_url == "":
        return redirect(url_for('execute'))
    session['github_url'] = github_url
    return redirect(url_for('fetch_repos'))

@app.route('/fetch_repos')
def fetch_repos():
    global repositories
    github_url = session.get('github_url')
    repositories = fetch_user_repositories(github_url)
    return redirect(url_for('calculate'))

@app.route('/calculate')    
def calculate():
    global repositories
    global responses
    if len(repositories) == 0:
        return redirect(url_for('display'))
    score = get_files_from_repo(repositories[-1])
    responses[repositories[-1]] = score

    # remove the last repository from the list
    repositories.pop()
    return redirect(url_for('wait'))

@app.route('/wait')
def wait():
    return redirect(url_for('calculate'))

@app.route('/display')
def display():
    # print the key with max value
    global responses
    repo_with_max_score = max(responses, key=responses.get)
    message = f"Repository with the maximum technical complexity is: {repo_with_max_score.name}"
    
    # return to explanation.html with the message
    return render_template('button.html', message=message, text="Generate explanation")

@app.route('/display', methods=['POST'])
def display_post():
    global responses
    repo_with_max_score = max(responses, key=responses.get)
    message1 = f"Repository with the maximum technical complexity is: {repo_with_max_score.name}"

    code_chunks, chunk_score, score = get_files_from_repo(repo_with_max_score, explanation=1)
    # recalculate the chunk scores, as they are currently cumulative
    chunk_score[1:] = [chunk_score[i] - chunk_score[i-1] for i in range(1, len(chunk_score))]

    # sort the chunks by score, also keeping the scores in the same order
    sorted_chunks = [x for _,x in sorted(zip(chunk_score, code_chunks), reverse=True)]
    sorted_scores = sorted(chunk_score, reverse=True)

    # keep the top 5 chunks, or all if there are less than 5
    if len(sorted_chunks) > 5:
        sorted_chunks = sorted_chunks[:5]
        sorted_scores = sorted_scores[:5]

    # pass the chunks to GPT-3 to generate explanations for why they are the most techincally complex
    explanations = []
    for i in range(len(sorted_chunks)):
        prompt = f"Explain why the repository with the following code is the most technically complex. Talk about the purpose of the code, as well as the code style and the various elements of the programming language, that have been used, and the syntax too. The score you gave for this chunk on a scale of 1-10 was {sorted_scores[i]} Answer in around 30 words:\n {sorted_chunks[i]} \n Explanation: "
        explanations.append(get_answer_from_gpt(prompt, 100))

    # combine the explanations into a single prompt and pass it to GPT-3 to generate a summary
    prompt = ""

    for i in explanations:
        prompt += i + "\n"

    prompt = "Summarize the combined explanations succintly:\n" + prompt + "Summary: "
    session['message1'] = message1
    session['prompt'] = prompt
    return redirect(url_for('explain'))

@app.route('/explain')
def explain():
    message1 = session.get('message1')
    prompt = session.get('prompt')
    message2 = get_answer_from_gpt(prompt, 100)

    message = message1 + "\n\n" + message2

    return render_template('button.html', message=message, text="Go to home page")

@app.route('/explain', methods=['POST'])
def explain_post():
    return redirect(url_for('execute'))

if __name__ == '__main__':
    app.run(debug=True)