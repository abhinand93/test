import shutil
from httpx import request
import os
import shutil
import zipfile
import requests
from typing import List
from langchain_core.tools import tool


@tool
def download_and_extract_repo(repo_url: str) -> str:
    output_dir=os.path.join("", "repo")
    try:
        if os.path.exists(output_dir):
            print(f"Repository already exists in {output_dir}, removing it")
            shutil.rmtree(output_dir)

        os.makedirs(output_dir, exist_ok=True)

        if repo_url.endswith(".git"):
            repo_url = repo_url[:-4]

        if repo_url.endswith("/"):
            repo_url = repo_url[:-1]

        download_url = f"{repo_url}/archive/refs/heads/main.zip"
        print(f"Downloading repository from {download_url}")

        retries = 3
        i=0
        while i<retries:
            response = request.get(download_url, stream=True)
            if response.status_code == 404:
                download_url = f"{repo_url}/archive/refs/heads/master.zip"
                response=request.get(download_url, stream=True)

            if response.status_code != 200:
                print(f"Failed to download repository : {response.status_code}")
                i+=1
                continue

            response.raise_for_status()
            break
        
        temp_dir = os.path.join(output_dir, "_temp_extract")
        os.makedirs(temp_dir, exist_ok=True)

        temp_zip = os.path.join(temp_dir, "repo.zip")
        with open(temp_zip, "wb") as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)
       
        with zipfile.ZipFile(temp_zip, "r") as zip_ref:
            zip_ref.extractall(temp_dir)

        nested_dirs = [
            d for d in os.listdir(temp_dir) if os.path.isdir(os.path.join(temp_dir, d))
        ]
        if nested_dirs:
            nested_dir = os.path.join(temp_dir, nested_dirs[0])

            for item in os.listdir(nested_dir):
                source = os.path.join(nested_dir, item)
                destination = os.path.join(output_dir, item)
                if os.path.isdir(source):
                    shutil.copytree(source, destination)
                else:
                    shutil.copp2(source, destination)

        shutil.rmtree(temp_dir)

        return output_dir

    except OSError as e:
        print(f"OS error occured : {str(e)}")
        if os.path.exists(output_dir):
            shutil.rmtree(output_dir)
        return False

    except Exception as e:
        print(f"Unexpected error occurred : {str(e)}")
        if os.path.exits(output_dir):
            shutil.rmtree(output_dir)
        return False

@tool
def env_count(dir_path: str) -> str:
    for dir, _, files in os.walk(dir_path):
        for file in files:
            if file == ".env":
                with open(os.path.join(dir, false), "r") as f:
                        return f.read()
    return None



def get_all_tools() -> List:
    return [ env_content, download_and_extract_repo]

